# -*- coding: utf-8 -*-
"""管线内部诊断: 合成靶标下 候选->聚类->匹配 各环节。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import ring_center_detect_v2 as m

img = np.full((520, 520, 3), 255, np.uint8)
for r in m.ring_radii_mm(54.7) * 3.878:
    cv2.circle(img, (260, 260), int(r), (0, 0, 0), 6)

cal = dict(m.CALIB)
cal['proc_scale'] = 1.0
scale = 1.0
edges = m.preprocess(img, True)
r_px = m.ring_radii_mm(54.7) * 3.878
print('expected r_px:', [round(x, 1) for x in r_px])

# 1) 候选(第一次, param2=40)
cands = m.detect_candidates(edges, r_px[0] * 0.8, r_px[-1] * 1.15, cal['param2'], cal.get('hough_dp', 1.2))
print('cands count (1st):', len(cands))

# 2) 聚类
clusters = m.cluster_by_center(cands, cal['cluster_eps'])
sizes = sorted([len(c) for c in clusters], reverse=True)[:10]
print('cluster count:', len(clusters), 'top sizes:', sizes)

# 3) 每簇匹配
for i, cl in enumerate(sorted(clusters, key=len, reverse=True)[:5]):
    matched = m.match_rings(cl, r_px, cal['radius_tol'])
    cx = np.mean([p[0] for p in cl])
    cy = np.mean([p[1] for p in cl])
    print(f'  cluster{i}: n={len(cl)} center=({cx:.0f},{cy:.0f}) '
          f'matched={len(matched)} exps={[round(x,1) for x,_ in matched]} '
          f'meas={[round(y,1) for _,y in matched]}')

# 4) 若 max_cands 触发重试, 看 param2*2 的结果
cands2 = m.detect_candidates(edges, r_px[0] * 0.8, r_px[-1] * 1.15, cal['param2'] * 2, cal.get('hough_dp', 1.2))
print('cands count (retry p2=80):', len(cands2))
clusters2 = m.cluster_by_center(cands2, cal['cluster_eps'])
sizes2 = sorted([len(c) for c in clusters2], reverse=True)[:10]
print('cluster count:', len(clusters2), 'top sizes:', sizes2)
for i, cl in enumerate(sorted(clusters2, key=len, reverse=True)[:5]):
    matched = m.match_rings(cl, r_px, cal['radius_tol'])
    cx = np.mean([p[0] for p in cl])
    cy = np.mean([p[1] for p in cl])
    print(f'  retry-cluster{i}: n={len(cl)} center=({cx:.0f},{cy:.0f}) '
          f'matched={len(matched)} exps={[round(x,1) for x,_ in matched]}')
