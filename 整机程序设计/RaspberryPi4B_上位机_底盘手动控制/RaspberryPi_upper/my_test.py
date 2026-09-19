from models.MoveControl import MoveControl

mc = MoveControl("/dev/ttyUSB0", 115200)

print("已发握手，请按小车上的 KEY1 ...")
mc.wait_for_start_cmd()
print("① 握手+回执 通过!")

mc.adjustHeightManual_mm(-20)
print("② 升降下降20mm 通过!")

mc.adjustHeightManual_mm(20)
print("③ 升降上升20mm 通过!")

mc.grubBlockFromRawArea()
print("④ 完整抓取 通过! 全部OK")
