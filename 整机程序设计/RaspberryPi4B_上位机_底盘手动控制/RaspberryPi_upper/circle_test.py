from models.Detection import Detection

det = Detection(1)
det.set_debug(True)
det.get_colored_circle_center('red', min_r=40)
