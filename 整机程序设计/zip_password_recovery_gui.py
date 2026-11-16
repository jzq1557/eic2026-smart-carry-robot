#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zip_password_recovery_gui.py —— ZIP 密码恢复工具(图形界面)
=========================================================
依赖: PyQt6 + 同目录下的 zip_password_recovery.py
运行: python zip_password_recovery_gui.py
仅用于恢复你自己创建(或你有权访问)的 zip 文件, 请勿用于他人文件。

功能:
  * 拖入/浏览选择 zip, 自动识别加密类型(ZipCrypto / WinZip AES)
  * 字符集穷举 / 字典攻击 / 候选密码列表 三种模式
  * 实时进度条、速度、剩余时间, 可随时停止
  * 找到后自动完整校验并支持一键复制密码
"""

import os
import sys
import time

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QProgressBar, QPlainTextEdit,
    QGroupBox, QRadioButton, QButtonGroup, QStackedWidget, QHBoxLayout,
    QVBoxLayout, QGridLayout, QFileDialog, QMessageBox,
)

import zipfile

import zip_password_recovery as core

STYLE = """
QGroupBox { font-weight: bold; margin-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QProgressBar { border: 1px solid #b0b0b0; border-radius: 4px; text-align: center; min-height: 18px; }
QProgressBar::chunk { background-color: #2d7dd2; border-radius: 3px; }
QPushButton#startBtn { background-color: #2d7dd2; color: white; font-weight: bold;
                       padding: 6px 20px; border-radius: 4px; }
QPushButton#startBtn:disabled { background-color: #9bb8d8; }
QPushButton#stopBtn { background-color: #d2544a; color: white; font-weight: bold;
                      padding: 6px 20px; border-radius: 4px; }
QPushButton#stopBtn:disabled { background-color: #d8a5a0; }
QLineEdit#resultEdit { color: #0a7d32; font-weight: bold; font-size: 14px;
                       border: 1px solid #4caf50; border-radius: 4px; padding: 4px; }
QPlainTextEdit { font-family: Consolas, "Microsoft YaHei", monospace; font-size: 12px; }
"""


class CrackWorker(QThread):
    """后台破解线程, 界面保持流畅, 可随时取消。"""
    progress = pyqtSignal(dict)
    log = pyqtSignal(str)
    result = pyqtSignal(bytes, int, int)   # 密码, 通过条目数, 失败条目数
    done = pyqtSignal(str)                 # found / notfound / cancelled / error

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zip_path = ''
        self.is_aes = False
        self.single = False
        self.jobs = 4
        self.batches = None
        self.total = None
        self._cancel = None
        self._last_emit = 0.0

    def setup(self, zip_path, is_aes, batches, total, jobs, single, cancel_event):
        self.zip_path = zip_path
        self.is_aes = is_aes
        self.batches = batches
        self.total = total
        self.jobs = jobs
        self.single = single
        self._cancel = cancel_event
        self._last_emit = 0.0

    def cancel(self):
        if self._cancel is not None:
            self._cancel.set()

    def run(self):
        if self.batches is None or self._cancel is None:
            self.done.emit('error')
            return

        def cb(info):
            now = time.time()
            if now - self._last_emit >= 0.1:   # 每秒最多发 10 次进度, 避免刷屏
                self._last_emit = now
                self.progress.emit(info)

        try:
            found, tried = core.search(
                self.zip_path, self.is_aes, self.batches,
                self.jobs, self.total,
                single=self.single,
                cancel_event=self._cancel,
                progress_cb=cb)
        except Exception as exc:
            self.log.emit('[!] 出错: %s' % exc)
            self.done.emit('error')
            return

        if self._cancel.is_set():
            self.log.emit('[-] 已停止, 共尝试 %s 个候选。' % format(tried, ','))
            self.done.emit('cancelled')
            return
        if found is not None:
            ok, bad = core.verify_password(self.zip_path, self.is_aes, found)
            self.result.emit(found, ok, bad)
            self.done.emit('found')
        else:
            self.log.emit('[-] 未找到密码, 共尝试 %s 个候选。' % format(tried, ','))
            self.done.emit('notfound')


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('ZIP 密码恢复工具(仅用于自己的文件)')
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)
        self.resize(800, 720)
        self.setMinimumSize(720, 640)
        self.worker = None
        self.cur_kind = None
        self.found_pwd = None
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # 文件选择
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel('zip 文件:'))
        self.zip_edit = QLineEdit()
        self.zip_edit.setPlaceholderText('选择或拖入 zip 文件...')
        self.zip_edit.editingFinished.connect(self._on_path_typed)
        file_row.addWidget(self.zip_edit, 1)
        self.browse_btn = QPushButton('浏览...')
        self.browse_btn.clicked.connect(self.pick_file)
        file_row.addWidget(self.browse_btn)
        root.addLayout(file_row)

        self.info_label = QLabel('未选择文件')
        self.info_label.setObjectName('infoLabel')
        self.info_label.setWordWrap(True)
        root.addWidget(self.info_label)

        # 攻击方式
        attack_box = QGroupBox('攻击方式')
        av = QVBoxLayout(attack_box)
        mode_row = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        for i, text in enumerate(['字符集穷举', '字典文件', '候选密码列表']):
            rb = QRadioButton(text)
            rb.setChecked(i == 0)
            self.mode_group.addButton(rb, i)
            rb.toggled.connect(self._on_mode_changed)
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        av.addLayout(mode_row)

        self.mode_stack = QStackedWidget()
        # 页 0: 字符集穷举
        p0 = QWidget()
        g0 = QGridLayout(p0)
        g0.addWidget(QLabel('字符集:'), 0, 0)
        self.charset_combo = QComboBox()
        self.charset_combo.setEditable(True)
        for label, value in [('纯数字 (digits)', 'digits'),
                             ('小写字母 (lower)', 'lower'),
                             ('大写字母 (upper)', 'upper'),
                             ('大小写字母 (letters)', 'letters'),
                             ('字母+数字 (alnum)', 'alnum'),
                             ('字母+数字+常用符号 (printable)', 'printable')]:
            self.charset_combo.addItem(label, value)
        self.charset_combo.setCurrentIndex(4)
        self.charset_combo.currentTextChanged.connect(self._update_total_label)
        g0.addWidget(self.charset_combo, 0, 1)
        g0.addWidget(QLabel('长度:'), 1, 0)
        len_row = QHBoxLayout()
        self.min_spin = QSpinBox()
        self.min_spin.setRange(1, 16)
        self.min_spin.setValue(1)
        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 16)
        self.max_spin.setValue(6)
        self.min_spin.valueChanged.connect(self._on_min_changed)
        self.max_spin.valueChanged.connect(self._on_max_changed)
        len_row.addWidget(self.min_spin)
        len_row.addWidget(QLabel('~'))
        len_row.addWidget(self.max_spin)
        len_row.addStretch(1)
        g0.addLayout(len_row, 1, 1)
        self.total_label = QLabel('候选总数: -')
        g0.addWidget(self.total_label, 2, 1)
        self.mode_stack.addWidget(p0)
        # 页 1: 字典文件
        p1 = QWidget()
        g1 = QGridLayout(p1)
        g1.addWidget(QLabel('字典文件:'), 0, 0)
        self.wordlist_edit = QLineEdit()
        self.wordlist_edit.setPlaceholderText('每行一个密码的 txt 文件')
        g1.addWidget(self.wordlist_edit, 0, 1)
        wl_btn = QPushButton('浏览...')
        wl_btn.clicked.connect(self.pick_wordlist)
        g1.addWidget(wl_btn, 0, 2)
        self.mangle_cb = QCheckBox('常见变形(大小写 + 常见后缀, 候选约 48 倍)')
        self.mangle_cb.setChecked(True)
        g1.addWidget(self.mangle_cb, 1, 1)
        self.mode_stack.addWidget(p1)
        # 页 2: 候选密码列表
        p2 = QWidget()
        g2 = QGridLayout(p2)
        g2.addWidget(QLabel('候选密码:'), 0, 0)
        self.passwords_edit = QLineEdit()
        self.passwords_edit.setPlaceholderText('用英文逗号分隔, 如 abc123,Abc@123,123456')
        g2.addWidget(self.passwords_edit, 0, 1)
        self.mode_stack.addWidget(p2)
        av.addWidget(self.mode_stack)
        root.addWidget(attack_box)

        # 高级选项
        adv_box = QGroupBox('高级选项')
        adv = QHBoxLayout(adv_box)
        adv.addWidget(QLabel('并行进程数:'))
        self.jobs_spin = QSpinBox()
        self.jobs_spin.setRange(1, 32)
        self.jobs_spin.setValue(min(core.mp.cpu_count(), 32))
        adv.addWidget(self.jobs_spin)
        adv.addSpacing(18)
        adv.addWidget(QLabel('密码编码:'))
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItems(['utf-8', 'gbk', 'gb18030', 'latin-1'])
        adv.addWidget(self.encoding_combo)
        adv.addSpacing(18)
        self.single_cb = QCheckBox('单进程模式(多进程异常时)')
        adv.addWidget(self.single_cb)
        adv.addStretch(1)
        root.addWidget(adv_box)

        # 操作按钮
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton('开始破解')
        self.start_btn.setObjectName('startBtn')
        self.start_btn.clicked.connect(self.start_crack)
        self.stop_btn = QPushButton('停止')
        self.stop_btn.setObjectName('stopBtn')
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_crack)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addSpacing(28)
        btn_row.addWidget(QLabel('测试单个密码:'))
        self.test_edit = QLineEdit()
        self.test_edit.setMaximumWidth(180)
        self.test_edit.returnPressed.connect(self.test_single)
        btn_row.addWidget(self.test_edit)
        test_btn = QPushButton('测试')
        test_btn.clicked.connect(self.test_single)
        btn_row.addWidget(test_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        # 进度
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)
        self.status_label = QLabel('就绪')
        root.addWidget(self.status_label)

        # 结果
        res_row = QHBoxLayout()
        self.result_label = QLabel('')
        res_row.addWidget(self.result_label)
        self.result_edit = QLineEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setObjectName('resultEdit')
        res_row.addWidget(self.result_edit, 1)
        self.copy_btn = QPushButton('复制')
        self.copy_btn.setVisible(False)
        self.copy_btn.clicked.connect(self.copy_result)
        res_row.addWidget(self.copy_btn)
        root.addLayout(res_row)

        # 日志
        root.addWidget(QLabel('日志:'))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFixedHeight(140)
        root.addWidget(self.log_view)

        self._update_total_label()

    # ------------------------------------------------------------- 文件相关
    def _on_path_typed(self):
        path = self.zip_edit.text().strip()
        if path:
            self.set_zip_path(path)

    def pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选择 zip 文件', '', 'Zip 文件 (*.zip);;所有文件 (*.*)')
        if path:
            self.set_zip_path(path)

    def pick_wordlist(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选择字典文件', '', '文本文件 (*.txt);;所有文件 (*.*)')
        if path:
            self.wordlist_edit.setText(path)

    def set_zip_path(self, path):
        path = os.path.abspath(path)
        self.zip_edit.setText(path)
        try:
            kind = core.inspect_zip(path)
            self.cur_kind = kind
            if kind == 'none':
                self.info_label.setText('该 zip 未加密, 无需密码即可直接解压。')
                self.info_label.setStyleSheet('color: #0a7d32;')
                self.log('[i] %s 未加密。' % os.path.basename(path))
                return
            if kind == 'aes':
                text = '加密类型: WinZip AES —— 每个候选都要跑 PBKDF2, 速度慢, 只适合小字典'
                color = '#b8860b'
            else:
                text = '加密类型: ZipCrypto —— 校验快, 适合字典 / 短密码穷举'
                color = '#0a7d32'
            with zipfile.ZipFile(path) as zf:
                enc = [i for i in zf.infolist() if i.flag_bits & 0x1]
                text += ' · 共 %d 个条目, 其中加密 %d 个' % (len(zf.infolist()), len(enc))
            self.info_label.setText(text)
            self.info_label.setStyleSheet('color: %s;' % color)
            self.log('[i] 已载入 %s (%s)' % (os.path.basename(path), 'AES' if kind == 'aes' else 'ZipCrypto'))
        except Exception as exc:
            self.cur_kind = None
            self.info_label.setText('无法读取该文件: %s' % exc)
            self.info_label.setStyleSheet('color: #d2544a;')
            self.log('[!] 读取失败: %s' % exc)

    # ------------------------------------------------------------- 拖拽
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.toLocalFile().lower().endswith('.zip'):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for u in event.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith('.zip'):
                self.set_zip_path(p)
                break

    # ------------------------------------------------------------- 模式/字符集
    def _on_mode_changed(self, checked):
        if checked:
            self.mode_stack.setCurrentIndex(self.mode_group.id(self.sender()))

    def current_charset(self):
        txt = self.charset_combo.currentText().strip()
        idx = self.charset_combo.findText(txt)
        if idx >= 0:
            data = self.charset_combo.itemData(idx)
            if data:
                return data
        return txt

    def _on_min_changed(self, v):
        if v > self.max_spin.value():
            self.max_spin.setValue(v)
        self._update_total_label()

    def _on_max_changed(self, v):
        if v < self.min_spin.value():
            self.min_spin.setValue(v)
        self._update_total_label()

    def _update_total_label(self):
        try:
            cs = ''.join(dict.fromkeys(core.expand_charset(self.current_charset())))
            mn, mx = self.min_spin.value(), self.max_spin.value()
            if not cs or mx < mn:
                self.total_label.setText('候选总数: -')
                return
            total = sum(len(cs) ** L for L in range(mn, mx + 1))
            self.total_label.setText('候选总数: %s' % format(total, ','))
        except Exception:
            self.total_label.setText('候选总数: -')

    # ------------------------------------------------------------- 运行控制
    def _build_batches(self):
        mode = self.mode_stack.currentIndex()
        encoding = self.encoding_combo.currentText()
        kwargs = dict(encoding=encoding, is_aes=(self.cur_kind == 'aes'))
        if mode == 0:
            cs = self.current_charset()
            if not cs:
                raise ValueError('请输入字符集。')
            kwargs.update(charset=cs,
                          min_len=self.min_spin.value(),
                          max_len=self.max_spin.value())
        elif mode == 1:
            wl = self.wordlist_edit.text().strip()
            if not wl:
                raise ValueError('请选择字典文件。')
            kwargs.update(wordlist=wl, wordlist_encoding=encoding,
                          mangle=self.mangle_cb.isChecked())
        else:
            pws = self.passwords_edit.text().strip()
            if not pws:
                raise ValueError('请输入候选密码(逗号分隔)。')
            kwargs.update(passwords=pws)
        return core.build_candidates(**kwargs)

    def start_crack(self):
        if self.worker is not None and self.worker.isRunning():
            return
        path = self.zip_edit.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, '提示', '请先选择有效的 zip 文件。')
            return
        if self.cur_kind in (None, 'none'):
            QMessageBox.information(self, '提示', '该 zip 未加密, 无需破解。')
            return
        if self.cur_kind == 'aes' and core.pyzipper is None:
            QMessageBox.critical(self, '需要 pyzipper',
                '该 zip 使用 WinZip AES 加密, 请先安装:\n\n'
                '    pip install pyzipper\n\n'
                '大规模 AES 穷举建议改用 hashcat(GPU):\n'
                '    zip2john 文件.zip > h.txt\n'
                '    hashcat -m 13600 h.txt dict.txt')
            return
        try:
            batches, total = self._build_batches()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '提示', str(exc))
            return
        if total is not None and total > 500_000_000:
            ret = QMessageBox.question(
                self, '候选空间很大',
                '候选总数约 %s 个, Python 可能耗时极长。\n建议缩小范围, 仍要继续吗?' % format(total, ','),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return

        if self.worker is not None:
            self.worker.deleteLater()
        self.found_pwd = None
        self.log('[+] 开始攻击 %s' % os.path.basename(path))
        if self.cur_kind == 'aes':
            self.log('[!] AES 加密速度慢, 仅适合小字典; 大规模穷举请用 hashcat。')
        cancel_event = __import__('threading').Event()
        self.worker = CrackWorker(self)
        self.worker.setup(path, self.cur_kind == 'aes', batches, total,
                          self.jobs_spin.value(), self.single_cb.isChecked(),
                          cancel_event)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self.log)
        self.worker.result.connect(self._on_result)
        self.worker.done.connect(self._on_done)
        self.worker.start()
        self._set_running(True)

    def stop_crack(self):
        if self.worker is not None and self.worker.isRunning():
            self.log('[*] 正在停止...')
            self.worker.cancel()

    def test_single(self):
        path = self.zip_edit.text().strip()
        pw = self.test_edit.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, '提示', '请先选择 zip 文件。')
            return
        if self.cur_kind in (None, 'none'):
            QMessageBox.information(self, '提示', '该 zip 未加密。')
            return
        if not pw:
            QMessageBox.warning(self, '提示', '请输入要测试的密码。')
            return
        if self.cur_kind == 'aes' and core.pyzipper is None:
            QMessageBox.critical(self, '需要 pyzipper', 'AES 加密需要: pip install pyzipper')
            return
        try:
            ok, bad = core.verify_password(
                path, self.cur_kind == 'aes',
                pw.encode(self.encoding_combo.currentText()))
        except Exception as exc:
            self.log('[!] 测试出错: %s' % exc)
            return
        if bad == 0 and ok > 0:
            self.log('[+] 密码正确: %r' % pw)
            self.status_label.setText('该密码正确: %s' % pw)
        else:
            self.log('[-] 密码错误: %r' % pw)
            self.status_label.setText('该密码错误')

    # ------------------------------------------------------------- 回调
    def _set_running(self, running):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.browse_btn.setEnabled(not running)
        if running:
            self.progress_bar.setValue(0)
            self.result_edit.clear()
            self.result_label.setText('')
            self.copy_btn.setVisible(False)
            self.status_label.setText('正在破解...')

    def _on_progress(self, info):
        tried = info['tried']
        total = info['total']
        rate = info['rate']
        eta = info['eta']
        if total:
            pct = min(100, int(tried * 100.0 / total))
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(pct)
        else:
            self.progress_bar.setRange(0, 0)   # 未知总量 -> 忙碌动画
        text = '已尝试 %s' % format(tried, ',')
        if total:
            text += ' / %s (%.1f%%)' % (format(total, ','),
                                        min(100.0, 100.0 * tried / total))
        text += ' · 速度 %s 个/秒' % format(int(rate), ',')
        if eta is not None:
            text += ' · 预计剩余 %s' % core.format_duration(eta)
        self.status_label.setText(text)

    def _on_result(self, pwd, ok, bad):
        shown = pwd.decode(self.encoding_combo.currentText(), errors='replace')
        self.found_pwd = shown
        self.result_edit.setText(shown)
        if bad == 0:
            self.result_label.setText('找到密码(已完整校验 %d 个条目):' % ok)
            self.log('[+] 找到密码: %r, 全部条目校验通过。' % shown)
        else:
            self.result_label.setText('找到部分条目密码(通过 %d / 失败 %d):' % (ok, bad))
            self.log('[+] 找到密码 %r, 但 %d 个条目未通过(可能每个文件密码不同)。' % (shown, bad))
        self.copy_btn.setVisible(True)

    def _on_done(self, state):
        self._set_running(False)
        if state == 'found':
            self.status_label.setText('完成: 找到密码')
        elif state == 'notfound':
            self.status_label.setText('完成: 未找到密码')
            self.progress_bar.setValue(0)
        elif state == 'cancelled':
            self.status_label.setText('已停止')
        else:
            self.status_label.setText('出错, 详见日志')

    def copy_result(self):
        if self.found_pwd:
            QApplication.clipboard().setText(self.found_pwd)
            self.status_label.setText('密码已复制到剪贴板: %s' % self.found_pwd)

    # ------------------------------------------------------------- 其他
    def log(self, msg):
        self.log_view.appendPlainText('[%s] %s' % (time.strftime('%H:%M:%S'), msg))

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            if not self.worker.wait(3000):
                self.worker.terminate()
                self.worker.wait(1000)
        event.accept()


# --------------------------------------------------------------------------
# 自测模式: python zip_password_recovery_gui.py --selftest <zip> <密码>
# --------------------------------------------------------------------------

def run_selftest(zip_path, expected):
    app = QApplication(sys.argv)
    w = MainWindow()
    w.set_zip_path(zip_path)
    w.charset_combo.setEditText(''.join(dict.fromkeys(expected)))
    w.min_spin.setValue(len(expected))
    w.max_spin.setValue(len(expected))
    w.show()
    for _ in range(10):
        app.processEvents()
    w.grab().save('gui_preview.png')
    print('SELFTEST: preview saved to gui_preview.png')

    holder = {}
    orig_done = w._on_done

    def wrapped_done(state):
        orig_done(state)
        holder['done'] = state

    w._on_done = wrapped_done

    # 测试 1: 找到密码
    w.start_crack()
    deadline = time.time() + 90
    while time.time() < deadline and not holder.get('done'):
        app.processEvents()
        time.sleep(0.05)
    if holder.get('done') == 'found' and w.found_pwd == expected:
        print('SELFTEST PASS (crack): found %r' % w.found_pwd)
    else:
        print('SELFTEST FAIL (crack): done=%r found=%r' % (holder.get('done'), w.found_pwd))
        return 1

    # 测试 2: 停止
    w.charset_combo.setEditText('digits')
    w.min_spin.setValue(1)
    w.max_spin.setValue(8)
    holder.clear()
    w.start_crack()
    time.sleep(2.0)
    for _ in range(10):
        app.processEvents()
    w.stop_crack()
    deadline = time.time() + 20
    while time.time() < deadline and not holder.get('done'):
        app.processEvents()
        time.sleep(0.05)
    if holder.get('done') == 'cancelled' and not w.worker.isRunning():
        print('SELFTEST PASS (cancel)')
        return 0
    print('SELFTEST FAIL (cancel): done=%r' % holder.get('done'))
    return 1


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == '--selftest':
        sys.exit(run_selftest(sys.argv[2], sys.argv[3]))
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
