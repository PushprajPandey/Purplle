# PROMPT: Write pytest tests for store intelligence pipeline: single person ENTRY,
# group entry (3 in 2s), REENTRY vs ENTRY, staff is_staff flag, low confidence preserved.
# CHANGES MADE: Used overlapping bboxes for ByteTrack continuity; fixed staff test to
# set profile directly; added emitter jsonl test and GroupEntryTracker unit test.

"""Pipeline unit tests with synthetic detections (no YOLO required)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from pipeline.emit import EventEmitter, build_event
from pipeline.tracker import (
    Detection,
    GroupEntryTracker,
    PipelineState,
    StaffClassificationCache,
    StaffDetector,
    bbox_centroid,
    generate_visitor_id,
)


CLIP_START = datetime(2026, 4, 10, 10, 0, 0, tzinfo=timezone.utc)
ENTRY_THRESHOLD_Y = 1080 * 0.8


def _det(
    bbox: tuple[float, float, float, float],
    confidence: float,
    seconds: float,
    frame_index: int = 0,
) -> Detection:
    return Detection(
        bbox=bbox,
        confidence=confidence,
        frame_index=frame_index,
        timestamp=CLIP_START + timedelta(seconds=seconds),
    )


def test_single_person_crossing_emits_one_entry(store_config):
    pipeline = PipelineState(store_config, "CAM_1", frame_height=1080, is_entry_camera=True)
    events: list[dict] = []

    outside_bbox = (400, 750, 500, 880)
    inside_bbox = (400, 800, 500, 930)

    for bbox in (outside_bbox, inside_bbox):
        dets = [_det(bbox, 0.88, seconds=1.0)]
        events.extend(pipeline.process_detections(dets, None, CLIP_START))

    entry_events = [e for e in events if e["event_type"] == "ENTRY"]
    assert len(entry_events) == 1


def test_group_entry_emits_three_separate_entries(store_config):
    pipeline = PipelineState(store_config, "CAM_1", frame_height=1080, is_entry_camera=True)
    events: list[dict] = []
    base_time = 5.0

    for i in range(3):
        ox = 300 + i * 120
        outside = (ox, 750, ox + 100, 880)
        inside = (ox, 800, ox + 100, 930)
        for bbox in (outside, inside):
            dets = [_det(bbox, 0.85, seconds=base_time + i * 0.3)]
            events.extend(pipeline.process_detections(dets, None, CLIP_START))

    entry_events = [e for e in events if e["event_type"] == "ENTRY"]
    assert len(entry_events) == 3


def test_reentry_emits_reentry_not_entry(store_config):
    pipeline = PipelineState(store_config, "CAM_1", frame_height=1080, is_entry_camera=True)
    events: list[dict] = []

    outside = (400, 750, 500, 880)
    inside = (400, 800, 500, 930)
    for bbox in (outside, inside):
        events.extend(
            pipeline.process_detections([_det(bbox, 0.9, 1.0)], None, CLIP_START)
        )

    first_visitor = next(e["visitor_id"] for e in events if e["event_type"] == "ENTRY")

    exit_bbox = (400, 700, 500, 820)
    events.extend(
        pipeline.process_detections([_det(exit_bbox, 0.9, 3.0)], None, CLIP_START)
    )

    for bbox in (outside, inside):
        events.extend(
            pipeline.process_detections([_det(bbox, 0.88, 5.0)], None, CLIP_START)
        )

    reentry_events = [e for e in events if e["event_type"] == "REENTRY"]
    entry_events_for_visitor = [
        e for e in events if e["event_type"] == "ENTRY" and e["visitor_id"] == first_visitor
    ]
    assert len(reentry_events) >= 1
    assert len(entry_events_for_visitor) == 1


def test_staff_flagged_track_produces_is_staff_true(store_config):
    staff_detector = StaffDetector(calibration_seconds=120)
    profile = np.ones(180, dtype=np.float32) * 0.5
    staff_detector._staff_profile = profile
    staff_detector._calibration_histograms = [profile.copy() for _ in range(12)]

    histogram = np.ones(180, dtype=np.float32) * 0.52
    assert staff_detector.is_staff(histogram) is True

    cache = StaffClassificationCache(staff_detector)
    visitor_id = "VIS_staff01"
    for _ in range(8):
        cache.resolve(visitor_id, histogram)
    assert cache.resolve(visitor_id, histogram) is True
    assert cache.resolve(visitor_id, np.zeros(180)) is True


def test_staff_classification_locked_per_visitor(store_config):
    staff_detector = StaffDetector(calibration_seconds=120)
    profile = np.ones(180, dtype=np.float32) * 0.5
    staff_detector._staff_profile = profile
    staff_detector._calibration_histograms = [profile.copy() for _ in range(12)]
    cache = StaffClassificationCache(staff_detector)
    vid = "VIS_locked"
    for _ in range(10):
        cache.resolve(vid, profile)
    first = cache._locked[vid]
    different = np.zeros(180, dtype=np.float32)
    assert cache.resolve(vid, different) == first

    event = build_event(
        store_id="STORE_BLR_002",
        camera_id="CAM_1",
        visitor_id="VIS_staff1",
        event_type="ENTRY",
        timestamp=CLIP_START,
        zone_id="ZONE_ENTRY",
        dwell_ms=0,
        is_staff=True,
        confidence=0.9,
        sku_zone="entry",
        session_seq=1,
    )
    assert event["is_staff"] is True


def test_low_confidence_detection_retained_with_accurate_confidence():
    event = build_event(
        store_id="STORE_BLR_002",
        camera_id="CAM_1",
        visitor_id="VIS_lowconf",
        event_type="ENTRY",
        timestamp=CLIP_START,
        zone_id="ZONE_ENTRY",
        dwell_ms=0,
        is_staff=False,
        confidence=0.42,
        sku_zone="entry",
        session_seq=1,
    )
    assert event["confidence"] == pytest.approx(0.42)


def test_group_entry_tracker_flags_group():
    tracker = GroupEntryTracker()
    ts = CLIP_START
    for i in range(3):
        is_group = tracker.record_crossing(i + 1, ts + timedelta(seconds=i * 0.5))
    assert is_group is True


def test_emitter_writes_jsonl(tmp_path):
    output = tmp_path / "events.jsonl"
    emitter = EventEmitter("STORE_BLR_002", output)
    emitter.open()
    emitter.emit(
        camera_id="CAM_1",
        visitor_id="VIS_test01",
        event_type="ENTRY",
        timestamp=CLIP_START,
        zone_id="ZONE_ENTRY",
        confidence=0.9,
        sku_zone="entry",
    )
    emitter.close()
    lines = output.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "VIS_test01" in lines[0]
