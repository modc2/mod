"""Optional auto pose estimation — a plug-in registry, not a dependency.

@ref never requires a model to work: scan() indexes everything and the
gimbal tags it. But if a local estimator IS installed, scan uses it to
pre-fill poses. Estimators register themselves here:

    @register('mediapipe')
    def my_estimator(path): return (yaw, pitch, roll) or None

Everything runs on this box — an estimator that phones an external API
does not belong in this file.
"""

ESTIMATORS = {}


def register(name):
    def deco(fn):
        ESTIMATORS[name] = fn
        return fn
    return deco


def available():
    return sorted(ESTIMATORS)


def estimate(path):
    """First registered estimator that produces a pose wins."""
    for name in available():
        try:
            pose = ESTIMATORS[name](path)
        except Exception:
            continue
        if pose:
            return pose
    return None


# ── mediapipe head-pose (registered only if mediapipe is installed) ──
try:
    import mediapipe as mp  # noqa: F401

    @register('mediapipe')
    def _mediapipe_head(path):
        """Rough head pose from face-mesh landmarks. Approximate on
        purpose — the gimbal exists to correct it."""
        import math
        import mediapipe as mp
        with mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True, max_num_faces=1) as mesh:
            import cv2
            img = cv2.imread(path)
            if img is None:
                return None
            res = mesh.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            if not res.multi_face_landmarks:
                return None
            lm = res.multi_face_landmarks[0].landmark
            # nose tip 1, left eye outer 33, right eye outer 263, chin 152
            nose, le, re, chin = lm[1], lm[33], lm[263], lm[152]
            mid_x, mid_y = (le.x + re.x) / 2, (le.y + re.y) / 2
            eye_span = max(abs(re.x - le.x), 1e-6)
            yaw = math.degrees(math.atan2((nose.x - mid_x) * 2.2, eye_span))
            pitch = -math.degrees(math.atan2((nose.y - mid_y) * 1.8, eye_span))
            roll = math.degrees(math.atan2(re.y - le.y, re.x - le.x))
            return (yaw, pitch, roll)
except ImportError:
    pass
