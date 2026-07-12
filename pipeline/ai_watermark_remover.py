"""AI 图片去水印模块
目前支持云端AI方案：
  - cloud: 火山引擎物体擦除API，效果好但需要配置AK/SK，有调用费用
传统算法（telea/ns）直接在 web/main.py 中使用OpenCV，无需额外依赖。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests


# ---------------------------------------------------------------------------
#  火山引擎云端智能擦除 API（物体/Logo/文字/水印去除）
#  文档：https://www.volcengine.com/docs/6791/134777
# ---------------------------------------------------------------------------
class VolcengineImageErase:
    """火山引擎智能擦除API（物体/水印/文字去除）"""

    def __init__(self, access_key: str, secret_key: str):
        self.access_key = access_key
        self.secret_key = secret_key
        self.host = "visual.volcengineapi.com"
        self.region = "cn-north-1"
        self.service = "cv"
        self.version = "2020-08-26"
        self.action = "EraseObject"  # 通用物体擦除（支持水印/Logo/文字）

    def _sign(self, key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    def _get_signature_key(self, date_stamp: str) -> bytes:
        k_date = self._sign(self.secret_key.encode("utf-8"), date_stamp)
        k_region = self._sign(k_date, self.region)
        k_service = self._sign(k_region, self.service)
        k_signing = self._sign(k_service, "request")
        return k_signing

    def remove(
        self,
        image: np.ndarray,
        regions: list[dict],
        padding: int = 2,
    ) -> np.ndarray:
        """调用火山引擎物体擦除API"""
        h, w = image.shape[:2]

        # 编码图片为base64
        success, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not success:
            raise RuntimeError("图片编码失败")
        img_b64 = base64.b64encode(buf.tobytes()).decode("utf-8")

        # 构建bbox数组，格式为 [x, y, w, h] 像素坐标
        bbox = []
        for r in regions:
            x = max(0, int(r["x"]) - padding)
            y = max(0, int(r["y"]) - padding)
            rw = int(r["w"]) + padding * 2
            rh = int(r["h"]) + padding * 2
            bbox.append([x, y, rw, rh])

        payload = {
            "image_base64": img_b64,
            "bbox": bbox,
        }
        payload_json = json.dumps(payload)

        # V4签名
        t = datetime.utcnow()
        amz_date = t.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = t.strftime("%Y%m%d")

        canonical_uri = "/"
        canonical_querystring = f"Action={self.action}&Version={self.version}"
        content_type = "application/json"

        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

        canonical_headers = (
            f"content-type:{content_type}\n"
            f"host:{self.host}\n"
            f"x-content-sha256:{payload_hash}\n"
            f"x-date:{amz_date}\n"
        )
        signed_headers = "content-type;host;x-content-sha256;x-date"

        canonical_request = (
            "POST\n"
            f"{canonical_uri}\n"
            f"{canonical_querystring}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{payload_hash}"
        )

        algorithm = "HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/{self.service}/request"
        string_to_sign = (
            f"{algorithm}\n"
            f"{amz_date}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        signing_key = self._get_signature_key(date_stamp)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization_header = (
            f"{algorithm} Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        headers = {
            "Content-Type": content_type,
            "Host": self.host,
            "X-Date": amz_date,
            "X-Content-Sha256": payload_hash,
            "Authorization": authorization_header,
        }

        url = f"https://{self.host}/?{canonical_querystring}"
        resp = requests.post(url, data=payload_json, headers=headers, timeout=60)

        if resp.status_code != 200:
            try:
                err_info = resp.json()
                msg = err_info.get("ResponseMetadata", {}).get("Error", {}).get("Message", resp.text[:300])
                code = err_info.get("ResponseMetadata", {}).get("Error", {}).get("Code", "")
                if "InvalidAction" in code or "NotFound" in code or "InvalidAccessKey" in code:
                    msg = "密钥错误或未开通服务，请检查AK/SK并在火山引擎控制台开通「智能图像处理」服务。"
            except Exception:
                msg = resp.text[:500]
            raise RuntimeError(f"云端API调用失败(HTTP {resp.status_code}): {msg}")

        resp_json = resp.json()
        # 火山引擎响应格式检查
        if "data" in resp_json:
            data = resp_json.get("data", {})
        elif "ResponseMetadata" in resp_json:
            err = resp_json.get("ResponseMetadata", {}).get("Error", {})
            raise RuntimeError(f"云端API错误: {err.get('Message', str(resp_json))[:300]}")
        else:
            data = resp_json

        img_b64_resp = data.get("image_base64") or data.get("image")
        if not img_b64_resp:
            raise RuntimeError(f"API返回中无图片数据")

        img_bytes = base64.b64decode(img_b64_resp)
        nparr = np.frombuffer(img_bytes, np.uint8)
        result = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if result is None:
            raise RuntimeError("返回图片解码失败")
        return result


def remove_watermark_cloud(
    image: np.ndarray,
    regions: list[dict],
    padding: int = 2,
    access_key: str | None = None,
    secret_key: str | None = None,
) -> np.ndarray:
    """使用火山引擎云端API去除水印"""
    ak = access_key or os.environ.get("VOLC_ACCESS_KEY", "")
    sk = secret_key or os.environ.get("VOLC_SECRET_KEY", "")

    if not ak or not sk:
        raise RuntimeError(
            "云端AI去水印需要配置火山引擎AK/SK。\n"
            "请在 .env 文件中添加：\n"
            "VOLC_ACCESS_KEY=你的AccessKey\n"
            "VOLC_SECRET_KEY=你的SecretKey\n"
            "获取地址：https://console.volcengine.com/iam/keymanage/\n"
            "并开通「智能图像处理-物体擦除」服务"
        )

    client = VolcengineImageErase(ak, sk)
    return client.remove(image, regions, padding=padding)
