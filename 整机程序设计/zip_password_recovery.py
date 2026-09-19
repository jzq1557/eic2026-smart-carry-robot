#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zip_password_recovery.py —— 找回自己打包的 zip 文件的解压密码
===========================================================
仅用于恢复你自己创建(或你有权访问)的 zip 文件, 请勿用于他人文件。

支持两种加密:
  * ZipCrypto 传统加密: 校验极快, 适合字典 + 短密码穷举
  * WinZip AES 加密(需 pip install pyzipper): 每个候选都要跑 PBKDF2, 速度慢,
    只适合小字典; 大规模 AES 穷举请改用 hashcat(GPU)

用法示例:
  # 先看加密类型
  python zip_password_recovery.py archive.zip --info

  # 测试单个 / 几个候选
  python zip_password_recovery.py archive.zip --test abc123
  python zip_password_recovery.py archive.zip --passwords abc123,Abc@123,123456

  # 字典攻击(每行一个密码), --mangle 做常见变形(大小写/加常见后缀)
  python zip_password_recovery.py archive.zip --wordlist dict.txt --mangle

  # 纯数字 4~6 位穷举
  python zip_password_recovery.py archive.zip --charset digits --min-len 4 --max-len 6

  # 小写字母+数字 1~6 位穷举(约 2.2 亿候选)
  python zip_password_recovery.py archive.zip --charset alnum --min-len 1 --max-len 6

内置字符集: digits / lower / upper / letters / alnum / printable
也可以直接写字符, 如 --charset abc123

其他提示:
  * 密码含中文时, 若 utf-8 找不到, 换 --encoding gbk 再试
  * 字典文件若为 GBK 编码, 加 --wordlist-encoding gbk
  * 更快的替代工具:
      ZipCrypto:  fcrackzip -b -c a1 -l 1-6 -u archive.zip
      AES:        zip2john archive.zip > h.txt && hashcat -m 13600 h.txt dict.txt
"""

import argparse
import itertools
import multiprocessing as mp
import os
import sys
import time
import zipfile

try:
    import pyzipper          # WinZip AES 支持
except ImportError:
    pyzipper = None

BUILTIN_CHARSETS = {
    'digits':    '0123456789',
    'lower':     'abcdefghijklmnopqrstuvwxyz',
    'upper':     'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
    'letters':   'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ',
    'alnum':     'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
    'printable': ('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
                  '!@#$%^&*()-_=+[]{};:,.<>/?`~'),
}

MANGLED_SUFFIXES = ('', '123', '1234', '123456', '1', '0', '!', '@', '#',
                    '.', '_', '520', '521', '666', '888', '007')


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def inspect_zip(path):
    """返回加密类型: 'none' / 'zipcrypto' / 'aes'"""
    with zipfile.ZipFile(path) as zf:
        encrypted = [i for i in zf.infolist() if i.flag_bits & 0x1]
        if not encrypted:
            return 'none'
        if any(i.compress_type == 99 for i in encrypted):
            return 'aes'
        return 'zipcrypto'


def open_zip(path, is_aes):
    if is_aes:
        if pyzipper is None:
            sys.exit('[!] 该 zip 使用 WinZip AES 加密, 请先安装: pip install pyzipper\n'
                     '    大规模 AES 穷举建议: zip2john 转哈希后用 hashcat -m 13600')
        return pyzipper.AESZipFile(path)
    return zipfile.ZipFile(path)


def verify_password(path, is_aes, pwd):
    """用密码 pwd 校验所有加密条目, 返回 (通过数, 失败数)。"""
    zf = open_zip(path, is_aes)
    try:
        zf.setpassword(pwd)
        ok = bad = 0
        for i in zf.infolist():
            if (i.flag_bits & 0x1) and not i.is_dir():
                try:
                    zf.read(i)
                    ok += 1
                except Exception:
                    bad += 1
        return ok, bad
    finally:
        zf.close()


def expand_charset(name):
    return BUILTIN_CHARSETS.get(name, name)


def read_wordlist(path, encoding):
    with open(path, 'r', encoding=encoding, errors='replace') as f:
        for line in f:
            w = line.strip()
            if w:
                yield w


def mangle(word):
    """把字典词扩展成常见变形: 大小写 + 常见后缀。"""
    seen = set()
    for form in (word, word.capitalize(), word.upper()):
        for suffix in MANGLED_SUFFIXES:
            v = form + suffix
            if v not in seen:
                seen.add(v)
                yield v


def format_duration(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return '%d小时%d分' % (h, m)
    if m:
        return '%d分%d秒' % (m, s)
    return '%d秒' % s


# --------------------------------------------------------------------------
# 多进程 worker: 每个进程只打开一次 zip, 批量尝试密码
# --------------------------------------------------------------------------

_CTX = {}


def _pick_target(infos):
    """选压缩后最小的加密条目用于快速校验。"""
    enc = [i for i in infos if not i.is_dir() and (i.flag_bits & 0x1)]
    return min(enc, key=lambda i: i.compress_size) if enc else None


def _worker_init(zip_path, is_aes, stop_event, cancel_event=None):
    _CTX['zf'] = open_zip(zip_path, is_aes)
    _CTX['info'] = _pick_target(_CTX['zf'].infolist())
    _CTX['stop'] = stop_event
    _CTX['cancel'] = cancel_event


def _try_batch(batch):
    """尝试一批密码, 返回 (命中的密码或 None, 本批已尝试个数)。"""
    stop = _CTX.get('stop')
    cancel = _CTX.get('cancel')
    if stop is not None and stop.is_set():
        return None, 0
    zf, info = _CTX['zf'], _CTX['info']
    tried = 0
    for pwd in batch:
        if cancel is not None and cancel.is_set():
            break
        tried += 1
        try:
            zf.setpassword(pwd)
            zf.read(info)
            if stop is not None:
                stop.set()
            return pwd, tried
        except Exception:
            if stop is not None and stop.is_set():
                break
    return None, tried


def _batches(stream, size, encoding):
    batch = []
    for s in stream:
        batch.append(s.encode(encoding, errors='replace'))
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _run_sequential(batches, zip_path, is_aes, cancel_event=None):
    _worker_init(zip_path, is_aes, None, cancel_event)
    for batch in batches:
        yield _try_batch(batch)


def _emit_progress(cb, tried, total, start):
    """把进度信息回调给外部(供 GUI 使用)。"""
    if cb is None:
        return
    elapsed = time.time() - start
    rate = tried / max(elapsed, 1e-9)
    eta = (total - tried) / rate if (total and rate > 0) else None
    cb({'tried': tried, 'total': total, 'rate': rate, 'eta': eta, 'elapsed': elapsed})


def build_candidates(passwords=None, wordlist=None, wordlist_encoding='utf-8',
                     mangle=False, charset=None, min_len=1, max_len=6,
                     encoding='utf-8', batch_size=0, is_aes=False):
    """构造候选密码批生成器和候选总数, 返回 (batches, total)。"""
    total = None
    if passwords:
        pws = passwords.split(',')
        total = len(pws) + 1

        def stream():
            yield ''
            for p in pws:
                yield p
    elif wordlist:
        n = sum(1 for _ in open(wordlist, 'rb'))
        total = n

        def stream():
            yield ''
            for w in read_wordlist(wordlist, wordlist_encoding):
                if mangle:
                    yield from mangle(w)
                else:
                    yield w
    elif charset:
        cs = ''.join(dict.fromkeys(expand_charset(charset)))
        if max_len < min_len:
            raise ValueError('--max-len 不能小于 --min-len')
        total = sum(len(cs) ** L for L in range(min_len, max_len + 1))

        def stream():
            yield ''
            for L in range(min_len, max_len + 1):
                for tup in itertools.product(cs, repeat=L):
                    yield ''.join(tup)
    else:
        raise ValueError('未指定候选来源')
    bs = batch_size or (20 if is_aes else 1000)
    return _batches(stream(), bs, encoding), total


def search(zip_path, is_aes, batches, jobs, total, single=False,
           cancel_event=None, progress_cb=None):
    start = time.time()
    tried = 0
    found = None
    last_show = 0.0
    is_tty = sys.stdout.isatty()

    def report(final=False):
        nonlocal last_show
        now = time.time()
        if not final and now - last_show < 2:
            return
        last_show = now
        rate = tried / max(now - start, 1e-9)
        line = '[*] 已尝试 %s' % format(tried, ',')
        if total:
            line += ' (%.2f%%)' % min(100.0, 100.0 * tried / total)
        line += ', 速度 %s 个/秒' % format(int(rate), ',')
        if total and rate > 0:
            line += ', 预计剩余 %s' % format_duration((total - tried) / rate)
        print(('\r' if is_tty else '\n') + line + '   ', end='', flush=True)

    if single:
        for pwd, n in _run_sequential(batches, zip_path, is_aes, cancel_event):
            tried += n
            if cancel_event is not None and cancel_event.is_set():
                break
            if pwd is not None:
                found = pwd
                break
            report()
            _emit_progress(progress_cb, tried, total, start)
        print()
        return found, tried

    mgr = stop = pool = None
    try:
        mgr = mp.Manager()
        stop = mgr.Event()
        pool = mp.Pool(jobs, initializer=_worker_init,
                       initargs=(zip_path, is_aes, stop))
        for pwd, n in pool.imap_unordered(_try_batch, batches, chunksize=1):
            tried += n
            if cancel_event is not None and cancel_event.is_set():
                if stop is not None:
                    stop.set()
                break
            if pwd is not None:
                found = pwd
                break
            report()
            _emit_progress(progress_cb, tried, total, start)
    except Exception as exc:
        if pool is not None:
            pool.terminate()
            pool.join()
            pool = None
        print('\n[!] 多进程不可用(%s), 退回单进程模式。' % exc)
        for pwd, n in _run_sequential(batches, zip_path, is_aes, cancel_event):
            tried += n
            if cancel_event is not None and cancel_event.is_set():
                break
            if pwd is not None:
                found = pwd
                break
            report()
            _emit_progress(progress_cb, tried, total, start)
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()
        if mgr is not None:
            mgr.shutdown()
    print()
    return found, tried


# --------------------------------------------------------------------------
# 参数与主流程
# --------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog='zip_password_recovery.py',
        description='找回自己打包的 zip 文件的解压密码(仅限本人/已授权文件)',
        epilog='内置字符集: %s' % ', '.join(BUILTIN_CHARSETS),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('zipfile', help='要恢复的 zip 文件路径')
    ap.add_argument('--info', action='store_true', help='只显示加密信息后退出')
    ap.add_argument('--test', metavar='PASSWORD', help='只测试这一个密码')
    ap.add_argument('--passwords', metavar='P1,P2,...', help='候选密码列表(逗号分隔)')
    ap.add_argument('--wordlist', metavar='FILE', help='字典文件, 每行一个密码')
    ap.add_argument('--mangle', action='store_true', help='对字典词做常见变形(约 48 倍候选)')
    ap.add_argument('--charset', metavar='CHARS', help='穷举字符集(内置名或直接写字符)')
    ap.add_argument('--min-len', type=int, default=1, help='穷举最短长度(默认 1)')
    ap.add_argument('--max-len', type=int, default=6, help='穷举最长长度(默认 6)')
    ap.add_argument('--encoding', default='utf-8', help='密码字节编码(默认 utf-8; 中文密码可试 gbk)')
    ap.add_argument('--wordlist-encoding', default='utf-8', help='字典文件编码(默认 utf-8)')
    ap.add_argument('--jobs', type=int, default=min(mp.cpu_count(), 32), help='并行进程数')
    ap.add_argument('--batch-size', type=int, default=0, help='每批候选数(默认 ZipCrypto 1000 / AES 20)')
    ap.add_argument('--single', action='store_true', help='强制单进程(多进程出问题时用)')
    return ap.parse_args(argv)


def main(argv=None):
    if not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    args = parse_args(argv)
    zip_path = args.zipfile
    if not os.path.isfile(zip_path):
        sys.exit('[!] 找不到文件: %s' % zip_path)

    try:
        ''.encode(args.encoding)
    except LookupError:
        sys.exit('[!] 未知编码: %s' % args.encoding)

    kind = inspect_zip(zip_path)
    is_aes = kind == 'aes'
    print('[*] 文件: %s' % zip_path)
    if kind == 'none':
        print('[*] 该 zip 没有加密, 无需密码即可直接解压。')
        return 0
    print('[*] 加密类型: %s' % ('WinZip AES (慢)' if is_aes else 'ZipCrypto (快)'))

    if args.info:
        with zipfile.ZipFile(zip_path) as zf:
            print('[*] 条目数: %d' % len(zf.infolist()))
            for i in zf.infolist():
                flag = '加密' if i.flag_bits & 0x1 else '未加密'
                print('      %-40s %s, 压缩后 %d 字节' % (i.filename, flag, i.compress_size))
        return 0

    if is_aes and pyzipper is None:
        sys.exit('[!] AES 加密需要 pyzipper:  pip install pyzipper\n'
                 '    或改用 hashcat(GPU, 快几个数量级):\n'
                 '      zip2john archive.zip > h.txt\n'
                 '      hashcat -m 13600 h.txt dict.txt')

    if args.test:
        pwd = args.test.encode(args.encoding)
        ok, bad = verify_password(zip_path, is_aes, pwd)
        if bad == 0 and ok > 0:
            print("[+] 密码正确: %r" % args.test)
            return 0
        print('[-] 密码错误。')
        return 1

    if args.max_len < args.min_len:
        sys.exit('[!] --max-len 不能小于 --min-len')

    total = None
    if args.passwords:
        pws = args.passwords.split(',')
        total = len(pws) + 1

        def stream():
            yield ''
            for p in pws:
                yield p
    elif args.wordlist:
        if not os.path.isfile(args.wordlist):
            sys.exit('[!] 找不到字典文件: %s' % args.wordlist)
        n = sum(1 for _ in open(args.wordlist, 'rb'))
        total = n
        note = ' (启用 --mangle, 实际候选约 %d 倍)' % (len(MANGLED_SUFFIXES) * 3) if args.mangle else ''
        print('[*] 字典共 %s 行%s' % (format(n, ','), note))

        def stream():
            yield ''
            for w in read_wordlist(args.wordlist, args.wordlist_encoding):
                if args.mangle:
                    yield from mangle(w)
                else:
                    yield w
    elif args.charset:
        cs = ''.join(dict.fromkeys(expand_charset(args.charset)))
        if not cs:
            sys.exit('[!] 字符集为空')
        print('[*] 字符集(%d 个字符): %s' % (len(cs), cs))
        total = sum(len(cs) ** L for L in range(args.min_len, args.max_len + 1))
        print('[*] 候选总数: %s (长度 %d~%d)' % (format(total, ','), args.min_len, args.max_len))
        if total > 500_000_000:
            print('[!] 候选空间很大, Python 速度可能不够; 建议缩小范围或改用 hashcat/GPU。')

        def stream():
            yield ''
            for L in range(args.min_len, args.max_len + 1):
                for tup in itertools.product(cs, repeat=L):
                    yield ''.join(tup)
    else:
        sys.exit('[!] 请指定一种攻击方式: --passwords / --wordlist / --charset, 或 --test 单测。')

    if is_aes:
        print('[!] AES 每个候选都要跑 PBKDF2, Python 只有每秒几十~几百个, 仅适合小字典;')
        print('    大规模 AES 穷举强烈建议 hashcat -m 13600。')

    batch_size = args.batch_size or (20 if is_aes else 1000)
    t0 = time.time()
    found, tried = search(zip_path, is_aes,
                          _batches(stream(), batch_size, args.encoding),
                          args.jobs, total, single=args.single)

    if found is not None:
        shown = found.decode(args.encoding, errors='replace')
        ok, bad = verify_password(zip_path, is_aes, found)
        if bad == 0 and ok > 0:
            print('[+] 找到密码: %r' % shown)
            print('[+] 全部 %d 个加密条目校验通过, 可以解压了。' % ok)
        else:
            print('[+] 找到可解开部分条目的密码: %r (通过 %d, 失败 %d)' % (shown, ok, bad))
            print('    该 zip 可能对不同条目使用了不同密码。')
    else:
        print('[-] 未找到密码。')
        print('    建议: 回忆密码的长度/字符习惯缩小范围, 换更大的字典, 或上 hashcat/GPU。')
    print('[*] 总耗时 %.1f 秒, 共尝试 %s 个候选。' % (time.time() - t0, format(tried, ',')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
