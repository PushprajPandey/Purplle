"""YOLOv8 person detection pipeline with ByteTrack and event emission."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_data_dir, get_store_config
from pipeline.emit import EventEmitter
from pipeline.tracker import PERSON_CLASS_ID, Detection, PipelineState

TARGET_FPS = 15
PERSON_CONF_THRESHOLD = 0.2
EMPTY_PERIOD_LOG_SECONDS = 300


def resolve_camera_config(store_config: dict, video_path: Path) -> tuple[str, dict, bool]:
    filename = video_path.name
    for camera in store_config.get("cameras", []):
        if camera.get("video_filename") == filename:
            is_entry = camera.get("role") == "entry"
            return camera["camera_id"], camera, is_entry
    camera_id = f"CAM_{video_path.stem.replace(' ', '_')}"
    is_entry_fallback = "1" in video_path.stem or "CAM 1" in filename
    return camera_id, {"yolo_model": "yolov8n.pt"}, is_entry_fallback


def get_yolo_model_name(camera_config: dict, is_entry: bool) -> str:
    if "yolo_model" in camera_config:
        return camera_config["yolo_model"]
    return "yolov8n.pt" if is_entry else "yolov8m.pt"


def parse_clip_start_time(video_path: Path) -> datetime:
    """
    Clip timestamps align with POS data (Brigade store 10-April-2026).
    Override with CLIP_REFERENCE_DATE=YYYY-MM-DD if needed.
    """
    clip_date = os.environ.get("CLIP_REFERENCE_DATE", "2026-04-10")
    year, month, day = (int(part) for part in clip_date.split("-"))
    return datetime(year, month, day, 10, 0, 0, tzinfo=timezone.utc)


def process_video(
    video_path: Path,
    store_id: str,
    output_path: Path | None = None,
    overwrite: bool = False,
) -> tuple[Path, int]:
    store_config = get_store_config(store_id)
    if store_config is None:
        raise ValueError(f"Unknown store_id: {store_id}")

    video_path = video_path.expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(
            f"Video file not found: {video_path}\n"
            "Use the full path, for example:\n"
            '  D:\\purplle\\CCTV Footage-20260529T160731Z-3-00144614ea\\CCTV Footage\\CAM 1.mp4\n'
            "(Do not use '...' from the README — that is only a placeholder.)"
        )

    camera_id, camera_config, is_entry = resolve_camera_config(store_config, video_path)
    model_name = get_yolo_model_name(camera_config, is_entry)

    from ultralytics import YOLO

    model = YOLO(model_name)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(
            f"OpenCV could not open video (file exists but may be corrupt): {video_path}"
        )

    source_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_skip = max(1, int(round(source_fps / TARGET_FPS)))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080

    clip_start = parse_clip_start_time(video_path)
    pipeline = PipelineState(store_config, camera_id, frame_height, is_entry)
    emitter = EventEmitter(store_id, output_path)
    if overwrite and emitter.output_path.exists():
        emitter.output_path.unlink()
    emitter.open(append=not overwrite)
    events_written = 0

    frame_index = 0
    processed_frames = 0
    last_detection_time = clip_start
    last_empty_log = clip_start

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_index % frame_skip != 0:
                frame_index += 1
                continue

            timestamp = clip_start + timedelta(seconds=frame_index / source_fps)
            results = model(
                frame,
                classes=[PERSON_CLASS_ID],
                conf=PERSON_CONF_THRESHOLD,
                verbose=False,
            )
            detections: list[Detection] = []

            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    confidence = float(box.conf[0].item())
                    detections.append(
                        Detection(
                            bbox=(x1, y1, x2, y2),
                            confidence=confidence,
                            frame_index=frame_index,
                            timestamp=timestamp,
                        )
                    )

            if detections:
                last_detection_time = timestamp
            else:
                empty_gap = (timestamp - last_detection_time).total_seconds()
                if empty_gap >= EMPTY_PERIOD_LOG_SECONDS:
                    if (timestamp - last_empty_log).total_seconds() >= EMPTY_PERIOD_LOG_SECONDS:
                        last_empty_log = timestamp

            raw_events = pipeline.process_detections(detections, frame, clip_start)
            for raw in raw_events:
                emitter.emit(
                    camera_id=raw["camera_id"],
                    visitor_id=raw["visitor_id"],
                    event_type=raw["event_type"],
                    timestamp=raw["timestamp"],
                    zone_id=raw.get("zone_id"),
                    dwell_ms=raw.get("dwell_ms", 0),
                    is_staff=raw.get("is_staff", False),
                    confidence=raw["confidence"],
                    sku_zone=raw.get("sku_zone", "entry"),
                    queue_depth=raw.get("queue_depth"),
                )
                events_written += 1

            frame_index += 1
            processed_frames += 1

    finally:
        cap.release()
        emitter.close()

    if events_written == 0:
        print(
            json.dumps(
                {
                    "warning": "no_events_emitted",
                    "camera_id": camera_id,
                    "video": str(video_path),
                    "hint": "YOLO found no persons above confidence threshold; clip may be empty or camera angle has no visible customers",
                }
            ),
            flush=True,
        )

    return emitter.output_path, events_written


def main() -> None:
    parser = argparse.ArgumentParser(description="Run person detection pipeline on CCTV footage")
    parser.add_argument("--video", required=True, help="Path to video file")
    parser.add_argument("--store_id", required=True, help="Store identifier from store_layout.json")
    parser.add_argument("--output", default=None, help="Optional output JSONL path")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace events_output.jsonl (use on first camera of a batch; later runs append)",
    )
    args = parser.parse_args()

    video_path = Path(args.video)
    output_path = Path(args.output) if args.output else None
    result_path, events_written = process_video(
        video_path,
        args.store_id,
        output_path,
        overwrite=args.overwrite,
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "events_output": str(result_path),
                "events_written": events_written,
            }
        )
    )


if __name__ == "__main__":
    main()
