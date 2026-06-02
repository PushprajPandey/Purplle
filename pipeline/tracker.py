"""ByteTrack multi-object tracking with Re-ID and staff detection."""

from __future__ import annotations

import hashlib
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

PERSON_CLASS_ID = 0
LOW_CONFIDENCE_THRESHOLD = 0.5
REENTRY_WINDOW_SECONDS = 30
REENTRY_TRAJECTORY_LENGTH = 10
REENTRY_COSINE_THRESHOLD = 0.85
GROUP_ENTRY_WINDOW_SECONDS = 2
GROUP_ENTRY_MIN_COUNT = 3
CROSS_CAMERA_SEEN_WINDOW_SECONDS = 60
STAFF_CALIBRATION_SECONDS = 120
STAFF_HISTOGRAM_MATCH_THRESHOLD = 0.92
STAFF_CALIBRATION_MAX_PEOPLE_IN_FRAME = 2
STAFF_CALIBRATION_MIN_SAMPLES = 12
STAFF_CLASSIFY_AFTER_FRAMES = 8
STAFF_DETECTION_ENABLED = (
    os.environ.get("STAFF_DETECTION_ENABLED", "true").lower() == "true"
)
ENTRY_PRESENCE_Y_RATIO = 0.55
STABLE_FRAMES_FOR_PRESENCE_ENTRY = 12
TRACK_LOST_SECONDS = 2.0
IOU_MATCH_THRESHOLD = 0.3
MIN_SECONDS_BETWEEN_ZONE_REENTER = 2.0


@dataclass
class Detection:
    bbox: tuple[float, float, float, float]
    confidence: float
    frame_index: int
    timestamp: datetime


@dataclass
class TrackState:
    track_id: int
    visitor_id: str
    bbox: tuple[float, float, float, float]
    confidence: float
    centroid_history: deque = field(
        default_factory=lambda: deque(maxlen=REENTRY_TRAJECTORY_LENGTH)
    )
    dominant_hsv: np.ndarray | None = None
    is_staff: bool = False
    last_seen: datetime | None = None
    first_seen: datetime | None = None
    crossed_entry_inward: bool = False
    crossed_entry_outward: bool = False
    has_exited: bool = False
    current_zone_id: str | None = None
    zone_enter_time: datetime | None = None
    last_dwell_emit: datetime | None = None
    in_billing_queue: bool = False
    billing_join_time: datetime | None = None
    below_threshold_side: str | None = None
    prev_centroid_y: float | None = None
    entry_emitted: bool = False
    zone_last_exit_time: dict[str, datetime] = field(default_factory=dict)


def bbox_centroid(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def bbox_iou(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_area = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    return inter_area / union if union > 0 else 0.0


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a_flat = a.astype(float).flatten()
    b_flat = b.astype(float).flatten()
    min_len = min(len(a_flat), len(b_flat))
    if min_len == 0:
        return 0.0
    a_flat = a_flat[:min_len]
    b_flat = b_flat[:min_len]
    denom = np.linalg.norm(a_flat) * np.linalg.norm(b_flat)
    if denom == 0:
        return 0.0
    return float(np.dot(a_flat, b_flat) / denom)


def trajectory_vector(centroids: deque) -> np.ndarray:
    if len(centroids) < 2:
        return np.array(list(centroids)[-1] if centroids else [0.0, 0.0])
    return np.array(list(centroids)).flatten()


def generate_visitor_id(first_seen: datetime, centroid: tuple[float, float]) -> str:
    seed = f"{first_seen.isoformat()}:{centroid[0]:.2f}:{centroid[1]:.2f}"
    digest = hashlib.md5(seed.encode()).hexdigest()
    return f"VIS_{digest[:6]}"


def extract_dominant_hsv(
    frame: np.ndarray, bbox: tuple[float, float, float, float]
) -> np.ndarray:
    import cv2

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in bbox]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return np.zeros(180, dtype=np.float32)

    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0], None, [180], [0, 180])
    cv2.normalize(hist, hist)
    return hist.flatten()


def point_in_polygon(
    point: tuple[float, float], polygon: list[list[float]]
) -> bool:
    x, y = point
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi
        ):
            inside = not inside
        j = i
    return inside


class ByteTracker:
    """IoU-based ByteTrack-style tracker assigning persistent track_ids."""

    def __init__(self) -> None:
        self._tracks: dict[int, TrackState] = {}
        self._next_track_id = 1

    def update(
        self, detections: list[Detection], frame: np.ndarray | None = None
    ) -> list[TrackState]:
        if not detections:
            return list(self._tracks.values())

        unmatched_tracks = set(self._tracks.keys())
        matched: list[tuple[int, Detection]] = []

        for det in sorted(detections, key=lambda d: d.confidence, reverse=True):
            best_track = None
            best_iou = IOU_MATCH_THRESHOLD
            for track_id in list(unmatched_tracks):
                iou = bbox_iou(self._tracks[track_id].bbox, det.bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_track = track_id
            if best_track is not None:
                matched.append((best_track, det))
                unmatched_tracks.discard(best_track)

        for track_id, det in matched:
            self._update_track(track_id, det, frame)

        for det in detections:
            if any(det is m[1] for m in matched):
                continue
            self._create_track(det, frame)

        return list(self._tracks.values())

    def _create_track(self, det: Detection, frame: np.ndarray | None) -> None:
        track_id = self._next_track_id
        self._next_track_id += 1
        centroid = bbox_centroid(det.bbox)
        visitor_id = generate_visitor_id(det.timestamp, centroid)
        track = TrackState(
            track_id=track_id,
            visitor_id=visitor_id,
            bbox=det.bbox,
            confidence=det.confidence,
            first_seen=det.timestamp,
            last_seen=det.timestamp,
        )
        track.centroid_history.append(centroid)
        if frame is not None:
            track.dominant_hsv = extract_dominant_hsv(frame, det.bbox)
        self._tracks[track_id] = track

    def _update_track(
        self, track_id: int, det: Detection, frame: np.ndarray | None
    ) -> None:
        track = self._tracks[track_id]
        track.bbox = det.bbox
        track.confidence = det.confidence
        track.last_seen = det.timestamp
        centroid = bbox_centroid(det.bbox)
        track.centroid_history.append(centroid)
        if frame is not None:
            track.dominant_hsv = extract_dominant_hsv(frame, det.bbox)

    def remove_stale(self, current_time: datetime) -> list[int]:
        stale_ids: list[int] = []
        for track_id, track in list(self._tracks.items()):
            if track.last_seen and (
                current_time - track.last_seen
            ).total_seconds() > TRACK_LOST_SECONDS:
                stale_ids.append(track_id)
                del self._tracks[track_id]
        return stale_ids

    def get_track(self, track_id: int) -> TrackState | None:
        return self._tracks.get(track_id)


class StaffDetector:
    """Detect staff via dominant HSV colour profile from calibration window."""

    def __init__(self, calibration_seconds: float = STAFF_CALIBRATION_SECONDS) -> None:
        self.calibration_seconds = calibration_seconds
        self._staff_profile: np.ndarray | None = None
        self._calibration_histograms: list[np.ndarray] = []

    def calibrate(
        self,
        histogram: np.ndarray,
        elapsed_seconds: float,
        people_in_frame: int,
    ) -> None:
        """Build uniform colour profile only during empty-store calibration window."""
        if elapsed_seconds > self.calibration_seconds:
            return
        if people_in_frame > STAFF_CALIBRATION_MAX_PEOPLE_IN_FRAME:
            return
        self._calibration_histograms.append(histogram)
        if len(self._calibration_histograms) >= STAFF_CALIBRATION_MIN_SAMPLES:
            self._staff_profile = np.mean(self._calibration_histograms, axis=0)

    def is_calibrated(self) -> bool:
        return (
            self._staff_profile is not None
            and len(self._calibration_histograms) >= STAFF_CALIBRATION_MIN_SAMPLES
        )

    def match_score(self, histogram: np.ndarray | None) -> float:
        if not self.is_calibrated() or histogram is None:
            return 0.0
        return cosine_similarity(self._staff_profile, histogram)

    def is_staff(self, histogram: np.ndarray | None) -> bool:
        return self.match_score(histogram) >= STAFF_HISTOGRAM_MATCH_THRESHOLD


class StaffClassificationCache:
    """Lock is_staff per visitor_id for the entire session (never flips)."""

    def __init__(self, staff_detector: StaffDetector) -> None:
        self._detector = staff_detector
        self._locked: dict[str, bool] = {}
        self._observation_count: dict[str, int] = {}

    def resolve(self, visitor_id: str, histogram: np.ndarray | None) -> bool:
        if not STAFF_DETECTION_ENABLED:
            self._locked[visitor_id] = False
            return False

        if visitor_id in self._locked:
            return self._locked[visitor_id]

        self._observation_count[visitor_id] = (
            self._observation_count.get(visitor_id, 0) + 1
        )
        if self._observation_count[visitor_id] < STAFF_CLASSIFY_AFTER_FRAMES:
            return False

        if not self._detector.is_calibrated():
            self._locked[visitor_id] = False
            return False

        is_staff = self._detector.is_staff(histogram)
        self._locked[visitor_id] = is_staff
        return is_staff

    def is_locked(self, visitor_id: str) -> bool:
        return visitor_id in self._locked


class ReIDMatcher:
    """Match re-entering visitors via centroid trajectory cosine similarity."""

    def __init__(self) -> None:
        self._exited_visitors: dict[str, dict[str, Any]] = {}

    def register_exit(
        self,
        visitor_id: str,
        trajectory: np.ndarray,
        entry_region: tuple[float, float],
        exit_time: datetime,
    ) -> None:
        self._exited_visitors[visitor_id] = {
            "trajectory": trajectory,
            "entry_region": entry_region,
            "exit_time": exit_time,
        }

    def match_reentry(
        self,
        trajectory: np.ndarray,
        entry_region: tuple[float, float],
        current_time: datetime,
    ) -> str | None:
        best_match: str | None = None
        best_score = REENTRY_COSINE_THRESHOLD

        for visitor_id, data in list(self._exited_visitors.items()):
            exit_time = data["exit_time"]
            if (current_time - exit_time).total_seconds() > REENTRY_WINDOW_SECONDS:
                del self._exited_visitors[visitor_id]
                continue

            region_dist = np.linalg.norm(
                np.array(entry_region) - np.array(data["entry_region"])
            )
            if region_dist > 150:
                continue

            score = cosine_similarity(trajectory, data["trajectory"])
            if score >= best_score:
                best_score = score
                best_match = visitor_id

        if best_match:
            del self._exited_visitors[best_match]
        return best_match


class CrossCameraTracker:
    """Skip duplicate ENTRY when visitor seen on entry camera within 60 seconds."""

    def __init__(self) -> None:
        self._seen_ids: dict[str, datetime] = {}

    def mark_seen(self, visitor_id: str, timestamp: datetime) -> None:
        self._seen_ids[visitor_id] = timestamp
        self._purge_old(timestamp)

    def was_recently_seen_on_entry(self, visitor_id: str, timestamp: datetime) -> bool:
        self._purge_old(timestamp)
        seen_at = self._seen_ids.get(visitor_id)
        if seen_at is None:
            return False
        return (timestamp - seen_at).total_seconds() <= CROSS_CAMERA_SEEN_WINDOW_SECONDS

    def _purge_old(self, current_time: datetime) -> None:
        expired = [
            vid
            for vid, ts in self._seen_ids.items()
            if (current_time - ts).total_seconds() > CROSS_CAMERA_SEEN_WINDOW_SECONDS
        ]
        for vid in expired:
            del self._seen_ids[vid]


class GroupEntryTracker:
    """Emit separate ENTRY for each bbox in group crossings (3+ in 2 seconds)."""

    def __init__(self) -> None:
        self._pending_crossings: deque = deque()

    def record_crossing(self, track_id: int, timestamp: datetime) -> bool:
        self._pending_crossings.append((track_id, timestamp))
        cutoff = timestamp - timedelta(seconds=GROUP_ENTRY_WINDOW_SECONDS)
        while self._pending_crossings and self._pending_crossings[0][1] < cutoff:
            self._pending_crossings.popleft()

        recent_track_ids = {item[0] for item in self._pending_crossings}
        is_group_entry = len(recent_track_ids) >= GROUP_ENTRY_MIN_COUNT
        return is_group_entry


class PipelineState:
    """Orchestrates tracking, Re-ID, staff detection, and zone logic."""

    def __init__(
        self,
        store_config: dict[str, Any],
        camera_id: str,
        frame_height: int,
        is_entry_camera: bool = False,
    ) -> None:
        self.store_config = store_config
        self.camera_id = camera_id
        self.frame_height = frame_height
        self.is_entry_camera = is_entry_camera
        self.tracker = ByteTracker()
        self.staff_detector = StaffDetector(
            calibration_seconds=store_config.get(
                "staff_calibration_seconds", STAFF_CALIBRATION_SECONDS
            )
        )
        self.reid_matcher = ReIDMatcher()
        self.cross_camera = CrossCameraTracker()
        self.group_entry = GroupEntryTracker()
        self.billing_zone_id = store_config.get("billing_zone_id", "ZONE_BILLING")
        self.zones = {
            z["zone_id"]: z for z in store_config.get("zones", []) if z["camera_id"] == camera_id
        }
        threshold_ratio = 0.8
        for cam in store_config.get("cameras", []):
            if cam["camera_id"] == camera_id:
                threshold_ratio = cam.get("entry_threshold_y_ratio", 0.8)
        self.entry_threshold_y = frame_height * threshold_ratio
        self.frame_height = frame_height
        self._last_detection_time: datetime | None = None
        self._pending_abandon_checks: list[dict[str, Any]] = []
        self.staff_cache = StaffClassificationCache(self.staff_detector)
        self._visitors_with_entry: set[str] = set()

    def process_detections(
        self,
        detections: list[Detection],
        frame: np.ndarray | None,
        clip_start: datetime,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        current_time = detections[0].timestamp if detections else clip_start

        if detections:
            self._last_detection_time = current_time
        elif self._last_detection_time:
            gap_seconds = (current_time - self._last_detection_time).total_seconds()
            if gap_seconds >= 300:
                self._last_detection_time = current_time

        tracks = self.tracker.update(detections, frame)
        elapsed = (current_time - clip_start).total_seconds()

        for det in detections:
            if det.confidence < LOW_CONFIDENCE_THRESHOLD:
                pass

        people_in_frame = len(detections)
        for track in tracks:
            if frame is not None and track.dominant_hsv is not None:
                self.staff_detector.calibrate(
                    track.dominant_hsv, elapsed, people_in_frame=people_in_frame
                )
            track.is_staff = self.staff_cache.resolve(
                track.visitor_id, track.dominant_hsv
            )

            zone_events = self._process_zones(track, current_time)
            events.extend(zone_events)

            if self.is_entry_camera:
                entry_events = self._process_entry_exit(track, current_time)
                events.extend(entry_events)
            else:
                floor_events = self._process_floor_camera(track, current_time)
                events.extend(floor_events)

        self.tracker.remove_stale(current_time)
        return events

    def _process_entry_exit(
        self, track: TrackState, current_time: datetime
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        centroid = bbox_centroid(track.bbox)
        cy = centroid[1]
        prev_cy = track.prev_centroid_y
        track.prev_centroid_y = cy

        if prev_cy is not None:
            crossed_inward = prev_cy < self.entry_threshold_y <= cy
            crossed_outward = prev_cy >= self.entry_threshold_y > cy
            if crossed_inward and not track.entry_emitted:
                events.extend(
                    self._emit_entry_or_reentry(track, current_time, centroid)
                )
            elif crossed_outward:
                events.extend(self._emit_exit(track, current_time, centroid))

        if cy >= self.entry_threshold_y and track.below_threshold_side != "inside":
            if track.below_threshold_side == "outside" and not track.entry_emitted:
                events.extend(
                    self._emit_entry_or_reentry(track, current_time, centroid)
                )
            track.below_threshold_side = "inside"

        elif cy < self.entry_threshold_y and track.below_threshold_side == "inside":
            events.extend(self._emit_exit(track, current_time, centroid))

        elif track.below_threshold_side is None:
            track.below_threshold_side = "outside" if cy < self.entry_threshold_y else "inside"

        presence_y = self.frame_height * ENTRY_PRESENCE_Y_RATIO
        if (
            len(track.centroid_history) >= STABLE_FRAMES_FOR_PRESENCE_ENTRY
            and cy >= presence_y
            and not track.entry_emitted
            and track.visitor_id not in self._visitors_with_entry
        ):
            events.extend(self._emit_entry_or_reentry(track, current_time, centroid))

        return events

    def _emit_entry_or_reentry(
        self,
        track: TrackState,
        current_time: datetime,
        centroid: tuple[float, float],
    ) -> list[dict[str, Any]]:
        if track.entry_emitted:
            return []

        reentry_visitor = self.reid_matcher.match_reentry(
            trajectory_vector(track.centroid_history),
            centroid,
            current_time,
        )
        if reentry_visitor:
            track.visitor_id = reentry_visitor
            track.has_exited = False
            event_type = "REENTRY"
            if self.staff_cache.is_locked(reentry_visitor):
                track.is_staff = self.staff_cache._locked[reentry_visitor]
        else:
            if track.visitor_id in self._visitors_with_entry:
                return []
            self.group_entry.record_crossing(track.track_id, current_time)
            event_type = "ENTRY"
            self._visitors_with_entry.add(track.visitor_id)
            self.cross_camera.mark_seen(track.visitor_id, current_time)

        track.entry_emitted = True
        track.crossed_entry_inward = True
        track.below_threshold_side = "inside"
        return [
            self._event_payload(
                track, event_type, current_time, "ZONE_ENTRY", track.confidence
            )
        ]

    def _emit_exit(
        self,
        track: TrackState,
        current_time: datetime,
        centroid: tuple[float, float],
    ) -> list[dict[str, Any]]:
        track.crossed_entry_outward = True
        track.has_exited = True
        track.entry_emitted = False
        track.below_threshold_side = "outside"
        self.reid_matcher.register_exit(
            track.visitor_id,
            trajectory_vector(track.centroid_history),
            centroid,
            current_time,
        )
        return [
            self._event_payload(
                track, "EXIT", current_time, "ZONE_ENTRY", track.confidence
            )
        ]

    def _process_floor_camera(
        self, track: TrackState, current_time: datetime
    ) -> list[dict[str, Any]]:
        return []

    def _process_zones(
        self, track: TrackState, current_time: datetime
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        centroid = bbox_centroid(track.bbox)

        for zone_id, zone in self.zones.items():
            inside = point_in_polygon(centroid, zone["polygon"])
            if inside and track.current_zone_id != zone_id:
                if self.is_entry_camera:
                    continue
                last_exit = track.zone_last_exit_time.get(zone_id)
                if last_exit is not None:
                    since_exit = (current_time - last_exit).total_seconds()
                    if since_exit < MIN_SECONDS_BETWEEN_ZONE_REENTER:
                        continue
                track.current_zone_id = zone_id
                track.zone_enter_time = current_time
                events.append(
                    self._event_payload(
                        track,
                        "ZONE_ENTER",
                        current_time,
                        zone_id,
                        track.confidence,
                        zone["sku_zone"],
                    )
                )
                if zone_id == self.billing_zone_id:
                    queue_depth = self._count_billing_queue()
                    if queue_depth > 0:
                        events.append(
                            self._event_payload(
                                track,
                                "BILLING_QUEUE_JOIN",
                                current_time,
                                zone_id,
                                track.confidence,
                                zone["sku_zone"],
                                queue_depth=queue_depth,
                            )
                        )
                        track.in_billing_queue = True
                        track.billing_join_time = current_time

            elif not inside and track.current_zone_id == zone_id:
                events.append(
                    self._event_payload(
                        track,
                        "ZONE_EXIT",
                        current_time,
                        zone_id,
                        track.confidence,
                        zone["sku_zone"],
                    )
                )
                if zone_id == self.billing_zone_id and track.in_billing_queue:
                    self._pending_abandon_checks.append(
                        {
                            "visitor_id": track.visitor_id,
                            "exit_time": current_time,
                            "track": track,
                            "zone_id": zone_id,
                            "sku_zone": zone["sku_zone"],
                        }
                    )
                    track.in_billing_queue = False
                track.zone_last_exit_time[zone_id] = current_time
                track.current_zone_id = None
                track.zone_enter_time = None

            elif inside and track.current_zone_id == zone_id:
                dwell_events = self._maybe_emit_dwell(track, zone, current_time)
                events.extend(dwell_events)

        return events

    def _maybe_emit_dwell(
        self, track: TrackState, zone: dict[str, Any], current_time: datetime
    ) -> list[dict[str, Any]]:
        if track.zone_enter_time is None:
            return []
        elapsed = (current_time - track.zone_enter_time).total_seconds()
        if track.last_dwell_emit is None:
            if elapsed >= 30:
                track.last_dwell_emit = current_time
                dwell_ms = int(elapsed * 1000)
                return [
                    self._event_payload(
                        track,
                        "ZONE_DWELL",
                        current_time,
                        zone["zone_id"],
                        track.confidence,
                        zone["sku_zone"],
                        dwell_ms=dwell_ms,
                    )
                ]
        else:
            since_last = (current_time - track.last_dwell_emit).total_seconds()
            if since_last >= 30:
                track.last_dwell_emit = current_time
                dwell_ms = int((current_time - track.zone_enter_time).total_seconds() * 1000)
                return [
                    self._event_payload(
                        track,
                        "ZONE_DWELL",
                        current_time,
                        zone["zone_id"],
                        track.confidence,
                        zone["sku_zone"],
                        dwell_ms=dwell_ms,
                    )
                ]
        return []

    def _count_billing_queue(self) -> int:
        count = 0
        for track in self.tracker._tracks.values():
            if track.current_zone_id == self.billing_zone_id and not track.is_staff:
                count += 1
        return count

    def _event_payload(
        self,
        track: TrackState,
        event_type: str,
        timestamp: datetime,
        zone_id: str,
        confidence: float,
        sku_zone: str = "entry",
        dwell_ms: int = 0,
        queue_depth: int | None = None,
    ) -> dict[str, Any]:
        track.is_staff = self.staff_cache.resolve(
            track.visitor_id, track.dominant_hsv
        )
        return {
            "camera_id": self.camera_id,
            "visitor_id": track.visitor_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": track.is_staff,
            "confidence": confidence,
            "sku_zone": sku_zone,
            "queue_depth": queue_depth,
        }
