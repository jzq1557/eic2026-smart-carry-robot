#!/usr/bin/python
# coding:utf-8

import cv2 as cv
import pyzbar.pyzbar as pyzbar
import serial
import numpy as np
from imutils import contours
import binascii
import time

global task1
global task2


def detect_red():
    lower_red1 = np.array([0, 43, 46])
    upper_red1 = np.array([10, 255, 255])

    lower_red2 = np.array([156, 43, 46])
    upper_red2 = np.array([180, 255, 255])

    while True:
        cap = cv.VideoCapture(0)
        if cap.isOpened() is True:
            ret, frame = cap.read()
            frame_required = frame[240:480, 220:420]
            if ret is True:
                frame_hsv = cv.cvtColor(frame_required, cv.COLOR_BGR2HSV)
                img1 = cv.inRange(frame_hsv, lower_red1, upper_red1)
                img2 = cv.inRange(frame_hsv, lower_red2, upper_red2)
                img = img1 + img2
                image, cnts, hier = cv.findContours(img, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
                if len(cnts) > 0:
                    s = []
                    max_index = 0
                    (cnts, boundingRects) = contours.sort_contours(cnts)
                    for cnt in cnts:
                        s.append(cv.contourArea(cnt))
                    max_index = s.index(max(s))
                    (x, y, w, h) = boundingRects[max_index]
                    if w > 100 and h > 180:
                        print("red")
                        cap.release()
                        return 'yes'
                    else:
                        continue


def detect_green():
    lower_green = np.array([35, 43, 46])
    upper_green = np.array([77, 255, 255])

    while True:
        cap = cv.VideoCapture(0)
        if cap.isOpened() is True:
            ret, frame = cap.read()
            frame_required = frame[240:480, 220:420]
            if ret is True:
                frame_hsv = cv.cvtColor(frame_required, cv.COLOR_BGR2HSV)
                img = cv.inRange(frame_hsv, lower_green, upper_green)
                image, cnts, hier = cv.findContours(img, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
                if len(cnts) > 0:
                    s = []
                    max_index = 0
                    (cnts, boundingRects) = contours.sort_contours(cnts)
                    for cnt in cnts:
                        s.append(cv.contourArea(cnt))
                    max_index = s.index(max(s))
                    (x, y, w, h) = boundingRects[max_index]
                    if w > 100 and h > 180:
                        print("green")
                        cap.release()
                        return 'yes'
                    else:
                        continue


def detect_blue():
    lower_blue = np.array([100, 43, 46])
    upper_blue = np.array([124, 255, 255])

    while True:
        cap = cv.VideoCapture(0)
        if cap.isOpened() is True:
            ret, frame = cap.read()
            frame_required = frame[240:480, 220:420]
            if ret is True:
                frame_hsv = cv.cvtColor(frame_required, cv.COLOR_BGR2HSV)
                img = cv.inRange(frame_hsv, lower_blue, upper_blue)
                image, cnts, hier = cv.findContours(img, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
                if len(cnts) > 0:
                    s = []
                    max_index = 0
                    (cnts, boundingRects) = contours.sort_contours(cnts)
                    for cnt in cnts:
                        s.append(cv.contourArea(cnt))
                    max_index = s.index(max(s))
                    (x, y, w, h) = boundingRects[max_index]
                    if w > 100 and h > 180:
                        print("blue")
                        cap.release()
                        return 'yes'
                    else:
                        continue


# 扫码函数
def detect_camera_scan_QRcode(capture):
    barcodeData = ''
    while True:
        ret, frame = capture.read()
        if ret is True:
            image_gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
            barcodes = pyzbar.decode(image_gray)
            for barcode in barcodes:
                barcodeData = barcode.data.decode("utf-8")
            if len(barcodeData) == 7:
                return barcodeData
            if cv.waitKey(1) & 0xFF == 27:
                break
        else:
            continue


def order_1(capture):
    global task
    task = ''
    task = str(detect_camera_scan_QRcode(capture))


# 圆环的识别
def circle_detect():
    cap = cv.VideoCapture(0)
    while True:
        ret, frame = cap.read()
        img_gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
        dst_img = cv.medianBlur(img_gray, 7)
        cap.release()
        return dst_img


def circle_center():
    while True:
        img = circle_detect()
        circles = cv.HoughCircles(img, cv.HOUGH_GRADIENT, 1, 50,
                                  param1=100, param2=100, minRadius=0, maxRadius=1000)
        try:
            P = circles[0]
        except TypeError:
            continue
        else:
            print("圆的个数是：")
            print(len(P))
            for i in P:
                r = int(i[2])
                x = int(i[0])
                y = int(i[1])

            print("圆心坐标为：", (x, y))
            print("圆的半径是：", r)
            # 按q退出
            if cv.waitKey(1) & 0xFF == ord('q'):
                break
            return x, y


def cmd_code():
    while True:
        cap = cv.VideoCapture(0)
        if cap.isOpened() is True:
            ret, img = cap.read()
            if ret is True:
                order_1(cap)
                if task != '':
                    cap.release()
                    break
                else:
                    continue
        else:
            continue
    breakout = 000


def cmd_grab1():
    for i in task1:
        print(i)
        if i == '1':
            arm_control(2)  # 转向物块并识别颜色的动作组编号为2
            arm_action_complete_or_not()
            result = detect_red()
            print('red')
            if result == 'yes':
                arm_control(3)  # 原料区抓取红色物料的动作组编号
                arm_action_complete_or_not()
            time.sleep(0.5)
        elif i == '2':
            arm_control(2)  # 转向物块并识别颜色的动作组编号为2
            arm_action_complete_or_not()
            result = detect_green()
            print("green")
            if result == 'yes':
                arm_control(4)  # 原料区抓取红色物料的动作组编号
                arm_action_complete_or_not()
        elif i == '3':
            arm_control(2)  # 转向物块并识别颜色的动作组编号为2
            arm_action_complete_or_not()
            result = detect_blue()
            print("blue")
            if result == 'yes':
                arm_control(5)  # 原料区抓取红色物料的动作组编号
                arm_action_complete_or_not()


# 粗加工区
def cmd_grab2():
    # 等待小车调整完成
    for i in task1:  # 放
        if i == '1':
            arm_control(6)
            arm_action_complete_or_not()
        elif i == '2':
            arm_control(7)
            arm_action_complete_or_not()
        elif i == '3':
            arm_control(8)
            arm_action_complete_or_not()
    for i in task1:  # 拿
        if i == '1':
            arm_control(9)
            arm_action_complete_or_not()
        elif i == '2':
            arm_control(10)
            arm_action_complete_or_not()
        elif i == '3':
            arm_control(11)
            arm_action_complete_or_not()


def cmd_grab3():
    # 等待小车调整完成
    for i in task1:
        print(int(i) + 11)
        arm_control(int(i) + 11)  # 从几开始写几
        arm_action_complete_or_not()


def cmd_grab4():
    # 等待小车调整完成
    for i in task2:
        if i == '1':
            arm_control(2)  # 转向物块并识别颜色的动作组编号为2
            arm_action_complete_or_not()
            result = detect_red()
            print('red')
            if result == 'yes':
                arm_control(3)  # 原料区抓取红色物料的动作组编号
                arm_action_complete_or_not()
            time.sleep(0.5)
        elif i == '2':
            arm_control(2)  # 转向物块并识别颜色的动作组编号为2
            arm_action_complete_or_not()
            result = detect_green()
            print("green")
            if result == 'yes':
                arm_control(4)  # 原料区抓取红色物料的动作组编号
                arm_action_complete_or_not()
        elif i == '3':
            arm_control(2)  # 转向物块并识别颜色的动作组编号为2
            arm_action_complete_or_not()
            result = detect_blue()
            print("blue")
            if result == 'yes':
                arm_control(5)  # 原料区抓取红色物料的动作组编号
                arm_action_complete_or_not()


def cmd_grab5():
    for i in task2:  # 放
        arm_control(int(i) + 5)  # 从几开始写几
        arm_action_complete_or_not()
    for i in task2:  # 拿
        arm_control(int(i) + 8)  # 从几开始写几
        arm_action_complete_or_not()


def cmd_grab6():
    for i in task2:
        arm_control(int(i) + 11)  # 从几开始写几
        arm_action_complete_or_not()

    # 发送数据


def send(data, ser):
    ser.write(chr(data).encode('utf-8'))


def arm_return():
    order = str(ord(ser_arm.read(1)))
    return order


def arm_control(num):
    for i in [0x55, 0x55, 0x05, 0x06, num, 0x01, 0x00]:
        send(i, ser_arm)


def arm_action_complete_or_not():
    order1 = ''
    while True:
        order1 = arm_return()
        while order1 == '':
            break
        else:
            if order1 == '5':
                order1 = arm_return()
                if order1 == '8':
                    ser_arm.read(3)
                    break
                else:
                    continue
            else:
                continue


# 发送数据到STM32
def send_command(data1):
    mw_set = [0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFE]
    mw_set[1] = data1
    ser_car.write(mw_set)
    time.sleep(0.1)


def send_data(data1, data2, data3):
    mw_set = [0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFE]
    mw_set[1] = data1 >> 8
    mw_set[2] = data1
    mw_set[3] = data2 >> 8
    mw_set[4] = data2
    mw_set[5] = data3 >> 8
    mw_set[6] = data3
    ser_car.write(mw_set)
    time.sleep(0.1)


# 接受数据来自STM32
def resive_data():
    len_return_data = ser_car.inWaiting()  # 获取缓冲数据（接收数据）长度
    re_data = ''
    if len_return_data != 0:
        print("one")
        rece_data = str(binascii.b2a_hex(ser_car.read(len_return_data)))
        re_data = rece_data[2:4]
        time.sleep(0.1)
    ser_car.flushInput()
    time.sleep(0.5)
    return re_data


# 靶心识别
def circle_fix():
    x, y = circle_center()
    send_data(x, y, 0)
    print(x)
    print(y)
    print("successful fixed!")


if __name__ == '__main__':
    ser_car = serial.Serial('/dev/ttyACM0', 115200, timeout=None)
    ser_arm = serial.Serial('/dev/ttyS0', 9600)
    print("port successful")

    # 扫码函数
    while True:
        arm_control(0)  # 回中的动作组编号为0
        arm_action_complete_or_not()
        send_command(0x60)  # 启动小车
        #  扫描二维码程序
        while True:
            r_data = resive_data()
            if r_data == '55':  # 到达拍照区
                arm_control(1)  # 转向二维码的动作组编号为1
                arm_action_complete_or_not()
                print("two")
                cmd_code()  # 扫码得到二维码信息
                print("three")               
                arm_control(0)  # 回中的动作组编号为0
                arm_action_complete_or_not()
                task1 = task[:3]
                task2 = task[4:]
                send_data(int(task1[0]), int(task1[1]), int(task1[2]))
                send_data(int(task2[0]), int(task2[1]), int(task2[2]))
                print("send successful")
                print(task)
                send_command(0x61)  # 拍照完成
                break
        # 转盘区 抓取函数
        while True:
            r_data = resive_data()
            if r_data == '56':
                cmd_grab1()
                print("successful2")
                arm_control(0)  # 回中的动作组编号为0
                arm_action_complete_or_not()
                send_command(0x62)  # 到转盘区完成
                break
                # 粗加工区 抓取函数
        while True:
            r_data = resive_data()
            if r_data == '57':  # 到达粗加工区
                cmd_grab2()
                arm_control(0)  # 回中的动作组编号为0
                arm_action_complete_or_not()
                print("successful3")
                send_command(0x63)  # 到粗加工区完成
                break
        # 精加工区 抓取函数
        while True:
            r_data = resive_data()
            if r_data == '58':
                print("successful4")
                cmd_grab3()
                arm_control(0)  # 回中的动作组编号为0
                arm_action_complete_or_not()
                send_command(0x64)  # 到粗加工区完成
                break
                # 第二圈  第二圈    第二圈   第二圈   第二圈
        # 转盘区 抓取函数
        while True:
            r_data = resive_data()
            if r_data == '59':
                cmd_grab4()
                print("successful5")
                arm_control(0)  # 回中的动作组编号为0
                arm_action_complete_or_not()
                send_command(0x65)  # 到转盘区完成
                break
        # 粗加工区 抓取函数
        while True:
            r_data = resive_data()
            if r_data == '60':
                cmd_grab5()
                print("successful6")
                arm_control(0)  # 回中的动作组编号为0
                arm_action_complete_or_not()
                send_command(0x66)  # 到转盘区完成
                break
                # 精加工区 抓取函数
        while True:
            r_data = resive_data()
            if r_data == '61':
                cmd_grab6()
                print("successful7")
                arm_control(0)  # 回中的动作组编号为0
                arm_action_complete_or_not()
                send_command(0x67)  # 到转盘区完成
                break
