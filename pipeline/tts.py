"""Step 3：字节 Seed-TTS 语音合成，逐场景生成 MP3。"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

from pipeline.helpers import PipelineLogger, retry


def synthesize_audio(
    api_key: str,
    scenes: list[dict[str, str]],
    output_dir: Path,
    logger: PipelineLogger,
    *,
    url: str | None = None,
    resource_id: str | None = None,
    speaker: str | None = None,
    audio_format: str = "mp3",
    sample_rate: int = 24000,
) -> list[str]:
    """按 scenes 顺序合成音频，返回本地音频路径列表。"""
    url = url or os.environ.get("TTS_API_URL",
        "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional")
    resource_id = resource_id or os.environ.get("TTS_RESOURCE_ID", "seed-tts-2.0")
    speaker = speaker or os.environ.get("TTS_SPEAKER", "zh_female_vv_uranus_bigtts")
    audio_format = audio_format or os.environ.get("TTS_FORMAT", "mp3")

    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "Content-Type": "application/json",
        "Connection": "keep-alive",
        "X-Control-Require-Usage-Tokens-Return": "*",
    }

    logger.info("step:tts.start", count=len(scenes), speaker=speaker, resource_id=resource_id)
    audio_paths: list[str] = []
    skipped = 0

    session = requests.Session()
    try:
        for scene in scenes:
            task_id = scene["id"]
            narration = scene["narration"]
            audio_path = output_dir / "audio" / f"{task_id}.{audio_format}"

            if audio_path.exists():
                logger.info("tts.skip_existing", task_id=task_id, path=str(audio_path))
                audio_paths.append(str(audio_path))
                skipped += 1
                continue

            logger.info("tts.progress", task_id=task_id, text=narration[:50])

            @retry(max_attempts=3, base_delay=1.5, on_retry=lambda e, a, d: logger.warn(
                "tts.retry", task_id=task_id, attempt=a, delay=round(d, 1)
            ))
            def synthesize_one(text: str = narration, tid: str = task_id) -> bytes:
                response = session.post(
                    url,
                    headers=headers,
                    json={
                        "req_params": {
                            "text": text,
                            "speaker": speaker,
                            "audio_params": {
                                "format": audio_format,
                                "sample_rate": int(sample_rate),
                            },
                        }
                    },
                    stream=True,
                    timeout=240,
                )
                audio_data = bytearray()
                try:
                    for chunk in response.iter_lines(decode_unicode=True):
                        if not chunk:
                            continue
                        data = json.loads(chunk)
                        code = data.get("code", 0)
                        if code == 0 and data.get("data"):
                            audio_data.extend(base64.b64decode(data["data"]))
                        elif code == 20000000:
                            break
                        elif code > 0:
                            raise RuntimeError(
                                f"TTS error code={code}: {json.dumps(data, ensure_ascii=False)[:200]}"
                            )
                finally:
                    response.close()
                if not audio_data:
                    raise RuntimeError(f"TTS 未返回音频数据 (task {tid})")
                return bytes(audio_data)

            try:
                audio_data = synthesize_one()
                audio_path.write_bytes(audio_data)
                if sys.platform != "win32":
                    os.chmod(audio_path, 0o644)
                audio_paths.append(str(audio_path))
            except Exception as exc:
                logger.error("tts.failed", task_id=task_id, error=str(exc)[:200])
                raise SystemExit(f"TTS 失败 (task {task_id}): {exc}") from exc
    finally:
        session.close()

    logger.info("step:tts.done", count=len(audio_paths), skipped=skipped,
                fresh=len(audio_paths) - skipped)
    return audio_paths


def synthesize_title_audio(
    api_key: str,
    text: str,
    output_path: Path,
    logger: PipelineLogger,
    *,
    url: str | None = None,
    resource_id: str | None = None,
    speaker: str | None = None,
    sample_rate: int = 24000,
) -> str:
    """标题卡专用：合成一小段短音频。逻辑与 synthesize_audio 相同，但只处理一次。"""
    if output_path.exists():
        logger.info("tts.title.skip_existing", path=str(output_path))
        return str(output_path)

    fake_scene = [{"id": output_path.stem, "narration": text}]
    audios = synthesize_audio(
        api_key,
        fake_scene,
        output_path.parent.parent,   # 让 synthesize_audio 拼出 audio/<id>.mp3
        logger,
        url=url,
        resource_id=resource_id,
        speaker=speaker,
        audio_format=output_path.suffix.lstrip("."),
        sample_rate=sample_rate,
    )
    return audios[0]
