"""HFD legacy feature equations, preserved for checkpoint parity.

Derived from HumanFallDetection, Copyright (c) 2020 Mohammad Taufeeque
and Samad Koita, MIT License; see HumanFallDetection/LICENSE.
The legacy gf derivative grouping is intentionally retained.
"""

import math

import numpy as np

FEATURE_LIST = ("ratio_bbox", "log_angle", "re", "ratio_derivative", "gf")


def pose_sample(observation):
    """Measured head/neck/hip only; reject missing/degenerate anatomy."""
    points = getattr(observation, 'coco_keypoints', None)
    if points is None:
        from features.fall_risk.pipeline.fall_backends import COCO_TO_MEDIAPIPE
        kp = observation.keypoints
        if kp is None or np.shape(kp) != (33, 3):return None
        points = np.zeros((17,3))
        for source,target in COCO_TO_MEDIAPIPE.items():points[source]=kp[target]
    points = np.asarray(points, dtype=float)
    if points.shape != (17,3) or not np.isfinite(points).all():return None
    head = points[[1,2,3,4]]
    if head[:,2].sum() < 1e-5 or np.any(points[[5,6,11,12],2] <= .005):return None
    H = np.average(head[:,:2],axis=0,weights=head[:,2])
    N = points[[5,6],:2].mean(axis=0)
    B = points[[11,12],:2].mean(axis=0)
    if min(np.linalg.norm(N-B),np.linalg.norm(H-B)) < 1e-6:return None
    box = getattr(observation,'model_bbox',None)
    if box is None:
        x,y,w,h = observation.bbox
        box = [[x,y],[x+w,y+h]]
    box = np.asarray(box,dtype=float)
    if box.shape!=(2,2) or not np.isfinite(box).all() or np.any(box[1]-box[0]<=0):return None
    ratio=float((box[1,0]-box[0,0])/(box[1,1]-box[0,1]))
    angle=get_angle_vertical(N-B)
    return dict(time=observation.pts_sec,keypoints=dict(H=H,N=N,B=B),box=box,
                features=dict(ratio_bbox=ratio,log_angle=float(np.log1p(abs(angle))),angle_vertical=angle))


def feature_vector(history, sample):
    features=sample['features']
    if history:
        if sample['time']<=history[-1]['time']:raise ValueError('HFD feature PTS must increase')
        features['re']=get_rot_energy(history[-1],sample)
        features['ratio_derivative']=get_ratio_derivative(history[-1],sample)
        if len(history)>=2:features['gf']=get_gf(history[-2],history[-1],sample)
    vector=np.array([features.get(name,0.) for name in FEATURE_LIST],dtype=np.float32)
    if not np.isfinite(vector).all():raise ValueError('Non-finite HFD feature')
    return vector

def get_angle(v0, v1):
    return math.atan2(np.linalg.det([v0, v1]), np.dot(v0, v1))

def get_rot_energy(ip0, ip1):
    t = ip1["time"] - ip0["time"]
    ip0 = ip0["keypoints"]
    ip1 = ip1["keypoints"]
    m1 = 1
    m2 = 5
    m3 = 5
    energy = 0
    den = 0
    N1 = ip1['N'] - ip1['B']
    N0 = ip0['N'] - ip0['B']
    d2sq = N1.dot(N1)
    w2sq = (get_angle(N0, N1)/t)**2
    energy += m2*d2sq*w2sq

    den += m2*d2sq
    H1 = ip1['H'] - ip1['B']
    H0 = ip0['H'] - ip0['B']
    d1sq = H1.dot(H1)
    w1sq = (get_angle(H0, H1)/t)**2
    energy += m1*d1sq*w1sq
    den += m1*d1sq

    energy = energy/(2*den)
    return energy

def get_angle_vertical(v):
    return math.atan2(-v[0], -v[1])

def get_gf(ip0, ip1, ip2):
    t1 = ip1["time"] - ip0["time"]
    t2 = ip2["time"] - ip1["time"]
    ip0 = ip0["keypoints"]
    ip1 = ip1["keypoints"]
    ip2 = ip2["keypoints"]

    m1 = 1
    m2 = 15
    g = 10
    H2 = ip2['H'] - ip2['N']
    H1 = ip1['H'] - ip1['N']
    H0 = ip0['H'] - ip0['N']
    d1 = np.sqrt(H1.dot(H1))
    theta_1_plus_2_2 = get_angle_vertical(H2)
    theta_1_plus_2_1 = get_angle_vertical(H1)
    theta_1_plus_2_0 = get_angle_vertical(H0)
    N2 = ip2['N'] - ip2['B']
    N1 = ip1['N'] - ip1['B']
    N0 = ip0['N'] - ip0['B']
    d2 = np.sqrt(N1.dot(N1))
    theta_2_2 = get_angle_vertical(N2)
    theta_2_1 = get_angle_vertical(N1)
    theta_2_0 = get_angle_vertical(N0)
    theta_1_0 = theta_1_plus_2_0 - theta_2_0
    theta_1_1 = theta_1_plus_2_1 - theta_2_1
    theta_1_2 = theta_1_plus_2_2 - theta_2_2


    theta2 = theta_2_1
    theta1 = theta_1_1

    del_theta1_0 = (get_angle(H0, H1))/t1
    del_theta1_1 = (get_angle(H1, H2))/t2

    del_theta2_0 = (get_angle(N0, N1))/t1
    del_theta2_1 = (get_angle(N1, N2))/t2
    del_theta1 = 0.5 * (del_theta1_1 + del_theta1_0)
    del_theta2 = 0.5 * (del_theta2_1 + del_theta2_0)

    doubledel_theta1 = (del_theta1_1 - del_theta1_0) / 0.5*(t1 + t2)
    doubledel_theta2 = (del_theta2_1 - del_theta2_0) / 0.5*(t1 + t2)

    d1 = d1/d2
    d2 = 1

    Q_RD1 = 0
    Q_RD1 += m1 * d1 * doubledel_theta1 * doubledel_theta1
    Q_RD1 += (m1*d1*d1 + m1*d1*d2*np.cos(theta1))*doubledel_theta2
    Q_RD1 += m1*d1*d2*np.sin(theta1)*del_theta2*del_theta2
    Q_RD1 -= m1*g*d2*np.sin(theta1+theta2)

    Q_RD2 = 0
    Q_RD2 += (m1*d1*d1 + m1*d1*d2*np.cos(theta1))*doubledel_theta1
    Q_RD2 += ((m1+m2)*d2*d2 + m1*d1*d1 + 2*m1*d1*d2*np.cos(theta1))*doubledel_theta2
    Q_RD2 -= 2*m1*d1*d2*np.sin(theta1)*del_theta2*del_theta1 + m1*d1 * \
        d2*np.sin(theta1)*del_theta1*del_theta1
    Q_RD2 -= (m1 + m2)*g*d2*np.sin(theta2) + m1*g*d1*np.sin(theta1 + theta2)

    return Q_RD1 + Q_RD2

def get_ratio_derivative(ip0, ip1):
    ratio_der = None
    time = ip1["time"] - ip0["time"]
    diff_box = ip1["features"]["ratio_bbox"] - ip0["features"]["ratio_bbox"]
    assert time != 0
    ratio_der = diff_box/time

    return ratio_der
