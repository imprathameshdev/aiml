"""Reference-image obstruction testing. Separate from vehicle AI and history."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
import cv2
import numpy as np
from .config import config

_lock = threading.RLock()
_states = {}

def _folder():
    return Path(config.DATA_DIR) / 'obstruction_testing'

def _config(camera_id):
    p = _folder() / f'{int(camera_id)}.json'
    if not p.exists():
        return None
    with p.open(encoding='utf-8') as handle:
        return json.load(handle)

def is_enabled(camera_id):
    with _lock:
        cfg = _config(camera_id)
        return bool(cfg and cfg.get('enabled'))

def signature(areas):
    return hashlib.sha256(json.dumps(areas, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def normalize_areas(areas):
    result = []
    if not areas:
        raise ValueError('Mark at least one area before saving an empty reference')
    for area in areas:
        points = area['points']
        if isinstance(points, str): points = json.loads(points)
        p = np.asarray(points, dtype=np.float32)
        if p.size != 8 or not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
            raise ValueError('Each area needs four corners normalized between 0 and 1')
        polygon = p.reshape(4, 2)
        if abs(cv2.contourArea(polygon)) < 0.0001:
            raise ValueError('Marked area is too small')
        result.append({'id': int(area['id']), 'points': [float(v) for v in p.reshape(-1)]})
    return sorted(result, key=lambda x: x['id'])

def _resize(frame):
    h, w = frame.shape[:2]
    target = min(w, 640)
    return cv2.resize(frame, (target, max(1, round(h * target / w))), interpolation=cv2.INTER_AREA)

def _write_config(camera_id, cfg):
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{int(camera_id)}.json'
    temp = folder / f'{int(camera_id)}.{uuid.uuid4().hex}.tmp'
    try:
        temp.write_text(json.dumps(cfg), encoding='utf-8')
        os.replace(temp, target)
    finally:
        if temp.exists(): temp.unlink()

def save_reference(camera_id, frame, areas, sensitivity=0.12):
    if not 0.02 <= sensitivity <= 0.5:
        raise ValueError('Sensitivity must be between 0.02 and 0.5')
    normalized = normalize_areas(areas)
    image = _resize(frame)
    with _lock:
        folder = _folder(); folder.mkdir(parents=True, exist_ok=True)
        filename = f'{int(camera_id)}-{uuid.uuid4().hex}.png'
        if not cv2.imwrite(str(folder / filename), image):
            raise RuntimeError('Could not save empty reference image')
        cfg = dict(enabled=True, image=filename, areas=normalized,
                   signature=signature(normalized), sensitivity=sensitivity,
                   saved_at=time.time(), shape=list(image.shape[:2]))
        _write_config(camera_id, cfg)
        _states.pop(int(camera_id), None)
        return cfg

def disable(camera_id):
    with _lock:
        cfg = _config(camera_id)
        if cfg:
            cfg['enabled'] = False
            _write_config(camera_id, cfg)
        _states.pop(int(camera_id), None)

def changed_fractions(reference, current, areas):
    """Fraction of changed pixels in each ROI; compensates a global brightness shift."""
    if current.shape[:2] != reference.shape[:2]:
        raise ValueError('Camera resolution/aspect changed; save a new empty reference')
    a = cv2.cvtColor(cv2.GaussianBlur(reference, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
    b = cv2.cvtColor(cv2.GaussianBlur(current, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
    shift = np.clip(np.median(b[:, :, 0] - a[:, :, 0]), -40, 40)
    b[:, :, 0] -= int(shift)
    difference = np.max(np.abs(b - a), axis=2)
    changed = (difference > 30).astype(np.uint8)
    changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    h, w = reference.shape[:2]
    result = {}
    for area in areas:
        points = np.asarray(area['points'], dtype=np.float32).reshape(4, 2)
        points = np.round(points * [w - 1, h - 1]).astype(np.int32)
        mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(mask, [points], 1)
        pixels = int(mask.sum())
        if pixels < 25: raise ValueError('Marked area is too small at preview resolution')
        result[area['id']] = float(np.count_nonzero(changed & mask)) / pixels
    return result

def evaluate(camera_id, frame, areas):
    normalized = normalize_areas(areas)
    with _lock:
        cfg = _config(camera_id)
        if not cfg or not cfg.get('enabled'): return None
        if cfg['signature'] != signature(normalized):
            raise ValueError('Marked areas changed; save a new empty reference')
        state = _states.get(camera_id)
        if not state or state['image'] != cfg['image']:
            reference = cv2.imread(str(_folder() / cfg['image']))
            if reference is None: raise ValueError('Empty reference image is unavailable')
            state = dict(image=cfg['image'], reference=reference, occupied={}, pending={})
            _states[camera_id] = state
        current = _resize(frame)
        scores = changed_fractions(state['reference'], current, normalized)
        output = {}
        for sid, fraction in scores.items():
            occupied = state['occupied'].get(sid, False)
            candidate = True if fraction >= cfg['sensitivity'] else False if fraction <= cfg['sensitivity'] * 0.5 else occupied
            if candidate == occupied:
                state['pending'][sid] = (candidate, 0)
            else:
                previous, count = state['pending'].get(sid, (candidate, 0))
                count = count + 1 if previous == candidate else 1
                state['pending'][sid] = (candidate, count)
                if count >= 3: occupied = candidate
            state['occupied'][sid] = occupied
            output[sid] = {'occupied': occupied, 'changed_fraction': fraction}
        return output

def _test_cameras():
    from . import database, models
    db = database.SessionLocal()
    try:
        result = []
        for cam in db.query(models.Camera).filter_by(is_enabled=True).all():
            if not is_enabled(cam.id): continue
            areas = [{'id': s.id, 'points': s.points} for s in db.query(models.Space).filter_by(camera_id=cam.id).all()]
            result.append(dict(id=cam.id, source=cam.source_type, url=cam.stream_url,
                user=cam.stream_user, password=cam.stream_password, res=cam.stream_resolution,
                fps=cam.stream_fps, areas=areas))
        return result
    finally: db.close()

async def monitor():
    """One shared monitor per server, independent of viewers and the AI interval."""
    from . import scheduler
    from .stream_manager import stream_manager
    cameras = []
    refresh = 0
    last_frames = {}
    while True:
        try:
            if time.monotonic() >= refresh:
                cameras = await asyncio.to_thread(_test_cameras)
                refresh = time.monotonic() + 2
            for cam in cameras:
                cid = cam['id']
                if not await asyncio.to_thread(is_enabled, cid): continue
                start = time.perf_counter()
                try:
                    if cam['source'] not in ('rtsp', 'video'):
                        raise ValueError('Obstruction testing requires a live camera/video')
                    await asyncio.to_thread(stream_manager.start_stream, cid, cam['source'], cam['url'],
                        cam['user'], cam['password'], res=cam['res'], fps=cam['fps'])
                    frame = stream_manager.get_latest_frame(cid, since=max(time.time()-3, last_frames.get(cid, 0)))
                    if frame is None:
                        if time.time()-last_frames.get(cid, 0) > 3: raise ValueError('Waiting for fresh camera frames')
                        continue
                    with stream_manager._lock:
                        captured = stream_manager.streams.get(cid, {}).get('last_frame_time', time.time())
                    result = await asyncio.to_thread(evaluate, cid, frame, cam['areas'])
                    last_frames[cid] = captured
                    # Do not publish a completed calculation after the user switches mode.
                    if result is None or not await asyncio.to_thread(is_enabled, cid): continue
                    previous = scheduler._state_cache.get(cid, {})
                    meta = dict(previous.get('metadata', {}))
                    meta.update(detection_mode='obstruction_test', detection_error=None,
                        detection_version=time.time_ns(), is_processing=False,
                        inference_speed=max(0.000001, time.perf_counter()-start),
                        timestamp=__import__('datetime').datetime.now(__import__('datetime').UTC).isoformat())
                    scheduler._state_cache[cid] = {**result, 'metadata': meta}
                except Exception as exc:
                    cache = scheduler._state_cache.setdefault(cid, {})
                    cache.setdefault('metadata', {}).update(detection_mode='obstruction_test',
                        detection_error=str(exc), is_processing=False, detection_version=time.time_ns())
        except asyncio.CancelledError: raise
        except Exception:
            # Keep the camera loop alive through temporary DB/file failures.
            refresh = 0
        await asyncio.sleep(0.25)
