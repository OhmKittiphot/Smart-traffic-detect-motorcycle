from typing import Optional

import cv2
import numpy as np


class CameraMotion:
    # Keeps the speed calibration's 4 points on the road when the camera moves in a video file.
    # Each frame is matched to the reference frame (ORB + RANSAC homography); if the view moved too far,
    # to the previous frame, chaining the motion. Returns the homography reference frame -> current frame.

    WORK_WIDTH = 960      # frames are matched at this width (full size is slower and no more accurate)
    MIN_INLIERS = 25      # fewer agreeing matches than this: the match is not trusted

    def __init__(self, reference_frame: np.ndarray):
        self._orb = cv2.ORB_create(nfeatures=1500)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self._ref = self._features(reference_frame)
        self._prev = None   # (features, homography) of the last frame matched, for chaining

    def _features(self, frame: np.ndarray):
        # (keypoints in full-frame pixels as an Nx2 array, descriptors) of a BGR frame.
        scale = min(1.0, self.WORK_WIDTH / frame.shape[1])
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if scale < 1.0:
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        keypoints, descriptors = self._orb.detectAndCompute(gray, None)
        points = np.array([kp.pt for kp in keypoints], dtype=np.float32) / scale
        return points, descriptors

    def _match(self, src, dst) -> Optional[np.ndarray]:
        # Homography mapping src's features onto dst's, or None when too few of them agree.
        if src[1] is None or dst[1] is None or len(src[1]) < 2 or len(dst[1]) < 2:
            return None
        pairs = self._matcher.knnMatch(src[1], dst[1], k=2)
        # Lowe's ratio test: keep a match only when it is clearly better than the second best
        good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
        if len(good) < self.MIN_INLIERS:
            return None
        src_pts = src[0][[m.queryIdx for m in good]]
        dst_pts = dst[0][[m.trainIdx for m in good]]
        # RANSAC leaves out the matches on moving vehicles, which do not follow the camera
        H, inliers = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 4.0)
        if H is None or inliers.sum() < self.MIN_INLIERS:
            return None
        return H

    def update(self, frame: np.ndarray) -> Optional[np.ndarray]:
        # Homography reference frame -> this frame, or None when the frame could not be matched.
        current = self._features(frame)
        H = self._match(self._ref, current)
        if H is None and self._prev is not None:
            prev_features, prev_H = self._prev
            step = self._match(prev_features, current)
            H = step @ prev_H if step is not None else None
        self._prev = (current, H) if H is not None else None
        return H

    def restart(self):
        # Forget the previous frame (after a seek it no longer comes right before the next one).
        self._prev = None
