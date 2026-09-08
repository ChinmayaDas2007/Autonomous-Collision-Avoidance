"""
Project Kessler - Edge Processing Payload
OpenCV Vision Pipeline (Phase 2: Frame Differencing & Streak Extraction)
-------------------------------------------------------------------------------
Implements the high-speed edge vision processing node:
1. TCP ingestion of synthetic optical Star Tracker frames from Vizard / core_phy.
2. Background subtraction (consecutive running differencing I_k - I_{k-1} and reference I_k - I_0)
   to eliminate static Tycho catalog stars and sensor thermal noise.
3. Morphological noise filtering and contour extraction to reject point-like noise
   and isolate true transient debris streaks.
4. Subpixel centroid extraction and 2D velocity vector estimation [v_x, v_y]
   to feed Phase 3 (Danger Corridor Projection).
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import struct
import time
from typing import Callable, List, Optional, Tuple, Deque
from collections import deque

try:
    import cv2
    import numpy as np
    OPENCV_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    OPENCV_AVAILABLE = False

from edge_pro.vision_interface import VisionPipelineInterface, DebrisDetection, _BoolCallable

logger = logging.getLogger("edge_pro.vision.opencv")


class OpenCVVisionPipeline(VisionPipelineInterface):
    """
    Production-grade implementation of Phase 2 Edge Vision for Project Kessler.
    Processes Star Tracker optical video frames using OpenCV background subtraction,
    contour filtering, and streak trajectory estimation.
    """

    def __init__(
        self,
        differencing_mode: str = "hybrid",  # "consecutive", "reference", or "hybrid"
        threshold_val: int = 25,
        min_streak_area: float = 3.0,
        min_aspect_ratio: float = 1.2,
        history_len: int = 15,
        target_asset: Optional[str] = None,
        on_detection_callback: Optional[Callable[[DebrisDetection], None]] = None,
    ):
        if not OPENCV_AVAILABLE:
            raise RuntimeError(
                "OpenCV and NumPy are required for OpenCVVisionPipeline. "
                "Ensure opencv-python and numpy are installed in your Python environment."
            )

        self.differencing_mode = differencing_mode
        self.threshold_val = threshold_val
        self.min_streak_area = min_streak_area
        self.min_aspect_ratio = min_aspect_ratio
        self.history_len = history_len
        self.target_asset = target_asset
        self.on_detection_callback = on_detection_callback

        self._active: bool = False
        self._attitude_locked: bool = False
        self._reference_frame: Optional[np.ndarray] = None
        self._previous_frame: Optional[np.ndarray] = None
        self._current_frame: Optional[np.ndarray] = None
        self._frame_count: int = 0

        # Circular buffer of timestamped detections: (timestamp_sec, cX, cY)
        self._history: Deque[Tuple[float, float, float]] = deque(maxlen=history_len)

        # Network stream receiver task handle
        self._stream_task: Optional[asyncio.Task] = None
        self._stream_running: bool = False
        self._detections: List[DebrisDetection] = []

    @property
    def is_active(self) -> _BoolCallable:
        return _BoolCallable(1 if self._active else 0)

    def get_detections(self) -> List[DebrisDetection]:
        return list(self._detections)

    @property
    def attitude_locked(self) -> bool:
        return self._attitude_locked

    @attitude_locked.setter
    def attitude_locked(self, locked: bool) -> None:
        self._attitude_locked = locked
        logger.info(f"[Vision] Attitude lock status set to: {locked}")

    async def arm_sensor(self, target_asset: str) -> None:
        """Arms the vision processing pipeline in preparation for observation window."""
        self.target_asset = target_asset
        self._reference_frame = None
        self._previous_frame = None
        self._current_frame = None
        self._frame_count = 0
        self._history.clear()
        self._detections.clear()
        logger.info(f"[Vision] Optical sensor armed for target asset: '{target_asset}'")

    async def start_tracking(self) -> None:
        """Engages the optical frame differencing pipeline."""
        self._active = True
        self._attitude_locked = True
        logger.info(
            f"[Vision] Optical tracking ENGAGED on '{self.target_asset}'. "
            f"Differencing mode: {self.differencing_mode}, Threshold: {self.threshold_val}"
        )

    async def stop_tracking(self) -> None:
        """Disengages tracking and releases frame buffers."""
        self._active = False
        self._attitude_locked = False
        self._reference_frame = None
        self._previous_frame = None
        logger.info("[Vision] Optical tracking DISENGAGED. Frame buffers flushed.")

    def reset_reference_frame(self, frame: Optional[np.ndarray] = None) -> None:
        """Resets or updates the static background reference starfield."""
        self._reference_frame = frame.copy() if frame is not None else None
        self._previous_frame = frame.copy() if frame is not None else None
        logger.info("[Vision] Background reference starfield frame updated.")

    def process_frame(
        self, frame_gray: np.ndarray, current_time: Optional[float] = None
    ) -> Optional[DebrisDetection]:
        """
        Executes Phase 2 computer vision pipeline on a single grayscale image:
        1. Background Subtraction: Eliminates static stars.
        2. Thresholding & Morphological Filtering: Strips thermal dark noise.
        3. Contour Extraction: Filters by area and aspect ratio to isolate streak.
        4. Subpixel Centroid & 2D Velocity Vector Estimation: Computes [v_x, v_y] for Phase 3.
        """
        if not self._active and not self._attitude_locked:
            return None

        if len(frame_gray.shape) == 3:
            frame_gray = cv2.cvtColor(frame_gray, cv2.COLOR_BGR2GRAY)

        self._current_frame = frame_gray.copy()
        now_sec = current_time or time.time()
        self._frame_count += 1

        # 1. Establish reference frame on first receipt
        if self._reference_frame is None:
            self._reference_frame = frame_gray.copy()
            self._previous_frame = frame_gray.copy()
            logger.info(
                f"[Vision] Frame {self._frame_count}: Initial reference starfield frame established "
                f"({frame_gray.shape[1]}x{frame_gray.shape[0]})."
            )
            return None

        # 2. Compute Difference Frame
        diff_frame = self._compute_differenced_image(frame_gray)

        # Update previous frame for next consecutive step
        self._previous_frame = frame_gray.copy()

        # 3. Threshold and Morphological Filtering
        _, thresh = cv2.threshold(diff_frame, self.threshold_val, 255, cv2.THRESH_BINARY)

        # Apply morphological opening to strip isolated 1-pixel shot noise
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        filtered = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

        # 4. Contour Extraction & Filtering
        contours, _ = cv2.findContours(filtered, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best_contour = None
        max_score = 0.0

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.min_streak_area:
                continue

            x, y, w, h = cv2.boundingRect(cnt)
            aspect_ratio = max(w, h) / max(1, min(w, h))

            # Score prioritizes elongated streaks with high pixel area
            score = area * math.sqrt(aspect_ratio)
            if score > max_score:
                max_score = score
                best_contour = cnt

        if best_contour is None:
            return None

        # 5. Centroid Extraction via Spatial Moments
        moments = cv2.moments(best_contour)
        if moments["m00"] <= 0:
            return None

        cX = float(moments["m10"] / moments["m00"])
        cY = float(moments["m01"] / moments["m00"])
        bx, by, bw, bh = cv2.boundingRect(best_contour)
        streak_len = float(math.hypot(bw, bh))
        contour_area = float(cv2.contourArea(best_contour))

        # 6. Trajectory & 2D Velocity Vector Estimation (Phase 3 input)
        vx, vy, speed, heading_deg = self._estimate_velocity(now_sec, cX, cY)
        self._history.append((now_sec, cX, cY))

        # Approximate signal-to-noise ratio
        mask = np.zeros_like(diff_frame)
        cv2.drawContours(mask, [best_contour], -1, 255, -1)
        streak_pixels = diff_frame[mask > 0]
        bg_pixels = diff_frame[mask == 0]
        mean_signal = float(np.mean(streak_pixels)) if len(streak_pixels) > 0 else 0.0
        std_bg = float(np.std(bg_pixels)) if len(bg_pixels) > 0 else 1.0
        snr_db = round(20.0 * math.log10(max(1.0, mean_signal) / max(1.0, std_bg)), 1)

        detection = DebrisDetection(
            detected=True,
            centroid_x=round(cX, 2),
            centroid_y=round(cY, 2),
            velocity_vx=round(vx, 2) if vx is not None else None,
            velocity_vy=round(vy, 2) if vy is not None else None,
            velocity_speed=round(speed, 2) if speed is not None else None,
            heading_angle_deg=round(heading_deg, 2) if heading_deg is not None else None,
            bounding_box=(bx, by, bw, bh),
            streak_length_px=round(streak_len, 2),
            contour_area=round(contour_area, 2),
            intensity_snr=snr_db,
            frame_index=self._frame_count,
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            target_asset=self.target_asset,
        )

        logger.info(
            f"[Vision Track] Debris Streak Isolated:\n"
            f"  Centroid: (X={detection.centroid_x}, Y={detection.centroid_y}) px\n"
            f"  Velocity: [Vx={detection.velocity_vx}, Vy={detection.velocity_vy}] px/s (Speed: {detection.velocity_speed} px/s)\n"
            f"  Heading:  {detection.heading_angle_deg}° | SNR: {detection.intensity_snr} dB"
        )

        self._detections.append(detection)

        if self.on_detection_callback:
            self.on_detection_callback(detection)

        return detection

    def _compute_differenced_image(self, current: np.ndarray) -> np.ndarray:
        """Applies consecutive, reference, or hybrid frame subtraction."""
        if self._reference_frame is None:
            self._reference_frame = current.copy()
        if self._previous_frame is None:
            self._previous_frame = current.copy()

        if self.differencing_mode == "consecutive":
            diff = cv2.absdiff(self._previous_frame, current)
        elif self.differencing_mode == "reference":
            diff = cv2.absdiff(self._reference_frame, current)
        else:  # hybrid
            # Combines consecutive differencing with reference differencing
            diff_ref = cv2.absdiff(self._reference_frame, current)
            diff_prev = cv2.absdiff(self._previous_frame, current)
            diff = cv2.bitwise_or(diff_ref, diff_prev)
        return diff

    def _estimate_velocity(
        self, now_sec: float, cX: float, cY: float
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Calculates 2D velocity vector and heading angle from recent history."""
        if not self._history:
            return None, None, None, None

        prev_t, prev_x, prev_y = self._history[-1]
        dt = now_sec - prev_t

        if dt <= 0.0001:
            return None, None, None, None

        vx = (cX - prev_x) / dt
        vy = (cY - prev_y) / dt
        speed = math.hypot(vx, vy)
        heading_deg = math.degrees(math.atan2(vy, vx))

        return vx, vy, speed, heading_deg

    def start_stream_client(self, host: str = "127.0.0.1", port: int = 5000) -> None:
        """Starts background TCP optical stream client task."""
        if self._stream_task is None or self._stream_task.done():
            self._stream_running = True
            self._stream_task = asyncio.create_task(
                self.run_tcp_stream_client(host=host, port=port)
            )

    def stop_stream_client(self) -> None:
        """Stops background TCP optical stream client task."""
        self._stream_running = False
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    async def run_tcp_stream_client(
        self, host: str = "127.0.0.1", port: int = 5000, retry_delay: float = 2.0
    ) -> None:
        """
        Asynchronous TCP client task that connects to the Vizard/core_phy video stream.
        Decodes incoming 5-byte framed JPEG/PNG payloads and feeds them to process_frame.
        """
        self._stream_running = True
        logger.info(f"[Vision TCP] Ingestion client starting on {host}:{port}...")

        while self._stream_running:
            reader, writer = None, None
            try:
                logger.info(f"[Vision TCP] Connecting to video stream at {host}:{port}...")
                reader, writer = await asyncio.open_connection(host, port)
                logger.info(f"[Vision TCP] Connected to video stream source.")

                while self._stream_running:
                    # Read 5-byte protocol header:
                    # 1 byte msg_type (0 = JSON control, 1 = JPEG image)
                    # 4 bytes payload_len (big-endian >I)
                    header = await reader.readexactly(5)
                    msg_type = header[0]
                    payload_len = struct.unpack(">I", header[1:5])[0]

                    payload = await reader.readexactly(payload_len)

                    if msg_type == 0:
                        # Control message
                        try:
                            ctrl = json.loads(payload.decode("utf-8"))
                            status = ctrl.get("status")
                            if status == "LOCKED":
                                self.attitude_locked = True
                                logger.info("[Vision TCP] Attitude lock confirmed by simulator.")
                        except Exception as e:
                            logger.warning(f"[Vision TCP] Error parsing control message: {e}")

                    elif msg_type == 1:
                        # Image frame (JPEG / PNG bytes)
                        if not self._attitude_locked and not self._active:
                            continue

                        nparr = np.frombuffer(payload, dtype=np.uint8)
                        frame = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
                        if frame is not None:
                            self.process_frame(frame)

            except (ConnectionRefusedError, asyncio.TimeoutError):
                logger.warning(f"[Vision TCP] Stream not reachable at {host}:{port}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
            except asyncio.IncompleteReadError:
                logger.info("[Vision TCP] Stream closed by peer.")
                await asyncio.sleep(retry_delay)
            except asyncio.CancelledError:
                break
            except Exception as ex:
                logger.exception(f"[Vision TCP] Stream read error: {ex}")
                await asyncio.sleep(retry_delay)
            finally:
                if writer:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

        logger.info("[Vision TCP] Ingestion client stopped.")

    def render_and_save_hud_frame(
        self,
        output_path: str,
        corridor_bounds: Optional[Tuple[float, float, float, float]] = None,
        detection: Optional[DebrisDetection] = None,
        is_breached: bool = False,
        action_decision: str = "PENDING",
    ) -> bool:
        """
        Renders the 2D 'Software Reality' monitor frame with:
          - Danger Corridor AABB box (Green = Clear, Red = Breach)
          - Detected debris centroid (cyan circle)
          - Directed velocity vector arrow (yellow)
          - Mission status HUD telemetry overlay
        Saves atomically to output_path for Streamlit Dashboard polling.
        """
        if not OPENCV_AVAILABLE:
            return False

        # Base frame: use current frame, reference frame, or blank canvas
        base = self._current_frame if self._current_frame is not None else self._reference_frame
        if base is None:
            base = np.zeros((480, 640), dtype=np.uint8)

        if len(base.shape) == 2:
            hud = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
        else:
            hud = base.copy()

        # 1. Draw Danger Corridor Box
        if corridor_bounds is not None:
            min_u, min_v, max_u, max_v = [int(round(c)) for c in corridor_bounds]
            box_color = (0, 0, 255) if is_breached else (0, 255, 0)
            cv2.rectangle(hud, (min_u, min_v), (max_u, max_v), box_color, 2)
            label = "DANGER CORRIDOR [BREACH]" if is_breached else "DANGER CORRIDOR [SECURE]"
            cv2.putText(hud, label, (min_u, max(15, min_v - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, box_color, 1)

        # 2. Draw Debris Centroid and Velocity Vector
        det = detection or (self._detections[-1] if self._detections else None)
        if det and det.centroid_x is not None and det.centroid_y is not None:
            cx, cy = int(round(det.centroid_x)), int(round(det.centroid_y))
            cv2.circle(hud, (cx, cy), 5, (255, 255, 0), -1)
            cv2.circle(hud, (cx, cy), 12, (255, 255, 0), 1)

            if det.velocity_vx is not None and det.velocity_vy is not None:
                arrow_end = (
                    int(round(cx + det.velocity_vx * 2.5)),
                    int(round(cy + det.velocity_vy * 2.5)),
                )
                cv2.arrowedLine(hud, (cx, cy), arrow_end, (0, 255, 255), 2, tipLength=0.3)
                vel_text = f"V=[{det.velocity_vx:.1f}, {det.velocity_vy:.1f}] px/s"
                cv2.putText(hud, vel_text, (cx + 15, cy - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

        # 3. Top Banner Overlay (Telemetry HUD)
        cv2.rectangle(hud, (0, 0), (hud.shape[1], 35), (20, 20, 20), -1)
        status_color = (0, 0, 255) if action_decision == "EXECUTE_BURN" else (0, 255, 0)
        cv2.putText(
            hud,
            f"PROJECT KESSLER AOID | ACTION: {action_decision}",
            (10, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            status_color,
            2,
        )
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        cv2.putText(hud, ts, (hud.shape[1] - 110, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

        # 4. Save Atomically
        try:
            out_file = Path(output_path)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = out_file.with_suffix(".tmp.jpg")
            cv2.imwrite(str(tmp_file), hud)
            os.replace(str(tmp_file), str(out_file))
            logger.info("Saved Software Reality HUD frame to %s", out_file)
            return True
        except Exception as err:
            logger.error("Failed to save HUD frame (%s)", err)
            return False


# Backward compatibility alias
OpenCVVisionProcessor = OpenCVVisionPipeline
