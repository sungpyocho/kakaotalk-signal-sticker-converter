import itertools
import json
from io import BytesIO
import os
import shutil
from pathlib import Path
from typing import Any, List, Optional, Tuple, cast
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from py_mini_racer import MiniRacer
from PIL import WebPImagePlugin, Image
import imagequant
from apng import APNG
import oxipng
import numpy as np

import decrypt

# 참고: https://github.com/laggykiller/sticker-convert/blob/master/src/sticker_convert/downloaders/download_kakao.py

JSINJECT = """
class osclass {
    android = true;
}
class uaclass {
    os = new osclass();
}
class util {
    static userAgent() {
        return new uaclass();
    }
}
class daumtools {
    static web2app(dataDict) {
        return dataDict['urlScheme'];
    }
}
class document {
    static querySelectorAll(selectors) {
        return [];
    }
}
"""
class CompressionOptions:
    color: Optional[int] = None
    max_size_static: int = 300000 # 300KB
    max_size_animated: int = 300000 # 300KB
    quality_min: int = 10
    quality_max: int = 100


class KakaoMetadata:
    @staticmethod
    def get_meta_info(
        title: str,
    ) -> Optional[dict[str, Any]]:
        meta_res = requests.get(f"https://e.kakao.com/api/v1/items/t/{title}")

        if meta_res.status_code == 200:
            meta_info = json.loads(meta_res.text)
        else:
            return None
    
        return meta_info

class KakaoEmoticonDownloader():
    def __init__(self):
        # emoticon settings
        self.url: Optional[str] = None
        self.id: Optional[str] = None
        self.title: Optional[str] = None
        self.author: Optional[str] = None
        self.info: Optional[dict[str, Any]] = None
        self.stickers_count: Optional[int] = 32

        # image file settings
        self.tmp_f: BytesIO = BytesIO()
        self.color: Optional[int] = 256
        self.quantize_method: Optional[str] = None

        # Compression options
        self.opt_comp = CompressionOptions()

    def optimize_png(self, image_bytes: bytes) -> bytes:
        return oxipng.optimize_from_memory(
            image_bytes,
            level=6,
            fix_errors=True,
            filter=[oxipng.RowFilter.Brute],
            optimize_alpha=True,
            strip=oxipng.StripChunks.safe(),
        )

    def convert_webp_to_apng(self, webp_file: str):
        print(f"Converting {webp_file} to APNG...")
        # ------------------------------
        # 0) 경로 처리 & 준비
        # ------------------------------
        fname_parent_path = Path(webp_file).parent
        fname_without_ext = Path(webp_file).stem
        fname_ext = Path(webp_file).suffix

        if fname_ext.lower() != ".webp":
            raise ValueError("This file is not a .webp file.")

        # Create the tmp folder to save the temporary frames
        temp_dir = os.path.join(fname_parent_path, "temp")
        os.makedirs(temp_dir, exist_ok=True)

        # ------------------------------
        # 1) WebP 애니메이션 → RGBA 프레임 리스트
        # ------------------------------
        im_webp = WebPImagePlugin.WebPImageFile(webp_file)
        frames_rgba = []
        nframes = 0

        while True:
            try:
                im_webp.seek(nframes)
                # 항상 RGBA (32비트)로 변환해 알파 포함
                frame = im_webp.convert("RGBA").resize((230, 230))
                frames_rgba.append(frame)
                nframes += 1
            except EOFError:
                break

        # ------------------------------
        # 2) 마스터 RGBA 이미지 생성
        #    (세로로 이어붙이는 예시)
        # ------------------------------
        width, height = frames_rgba[0].size
        total_height = height * len(frames_rgba)
        master_img = Image.new("RGBA", (width, total_height))
        for i, fr in enumerate(frames_rgba):
            master_img.paste(fr, (0, i*height))

        # save master_img
        master_img.save(f"{temp_dir}/{fname_without_ext}_master.png")
        
        # ------------------------------
        # 3) 마스터 이미지를 "한 번만" 팔레트화
        # ------------------------------
        with Image.fromarray(np.asarray(master_img), "RGBA") as image:
            resized_master_img = imagequant.quantize_pil_image(
                                    image,
                                    dithering_level=1,
                                    max_colors=self.color,
                                    min_quality=self.opt_comp.quality_min,
                                    max_quality=self.opt_comp.quality_max,
                                )
        resized_master_img.save(f"{temp_dir}/{fname_without_ext}_master.png")


        # ------------------------------
        # 4) 팔레트 적용된 마스터 이미지를
        #    프레임별로 crop & PNG 저장
        # ------------------------------
        paletted_frames = []
        for i in range(len(frames_rgba)):
            top = i * height
            bottom = top + height

            # 프레임별 crop
            frame_crop = resized_master_img.crop((0, top, width, bottom))

            # 프레임 png로 저장 (optimize=False로 팔레트 유지)
            frame_path = os.path.join(temp_dir, f"{fname_without_ext}_frame{i:03d}.png")
            frame_crop.save(frame_path, optimize=False)
            paletted_frames.append(frame_path)

        # ------------------------------
        # 5) APNG로 합치기
        # ------------------------------
        apng_fpath = os.path.join(fname_parent_path, f"{fname_without_ext}.apng")
        apng_img = APNG()

        for fpath in paletted_frames:
            apng_img.append_file(fpath, delay=100)

        apng_img.save(apng_fpath)

        # ------------------------------
        # 6) temp 폴더 삭제
        # ------------------------------
        shutil.rmtree(temp_dir)

        print("Conversion Finished.")

    def get_info_from_share_link(self, url):
        self.url = url
        headers = {"User-Agent":"Android"}
        response = requests.get(self.url, headers=headers)
        soup = BeautifulSoup(response.content.decode("utf-8", "ignore"), "html.parser")

        title_tag = soup.find("title")
        if not title_tag:
            raise ValueError("Could not find the title tag in the HTML.")
        
        item_title: str = title_tag.string

        js = ""
        for script_tag in soup.find_all("script"):
            js_found = script_tag.string
            if js_found and "daumtools.web2app" in js_found:
                break

        if "daumtools.web2app" not in js_found:
            # print html to debug
            print(soup.prettify().encode('utf-8'))
            raise ValueError("Could not find the daumtools.web2app function in the HTML.")
        
        js = JSINJECT + js_found
        ctx = MiniRacer()
        kakao_url = cast(str, ctx.eval(js))
        item_id = kakao_url.split("kakaotalk://store/emoticon/")[1].split("?")[0]

        # find author
        headers_desktop = {"User-Agent": "Chrome"}

        response = requests.get(url, headers=headers_desktop, allow_redirects=True)
        print(response.url)

        title_url = urlparse(response.url).path.split("/")[-1]
        meta_info = KakaoMetadata.get_meta_info(title_url)
        if meta_info:
            self.author = meta_info["result"]["artist"]
            self.stickers_count = len(meta_info["result"]["thumbnailUrls"])
            print("Found Author and number of stickers. Meta Info saved: ", self.author, self.stickers_count)

        if self.stickers_count == 0:
            raise ValueError("Stickers count is still 0. check function get_info_from_share_link().")

        self.title = item_title
        self.id = item_id

    def download_stickers(self):
        if not self.id:
            raise ValueError("Emoticon ID is not set.")
        if not self.title:
            raise ValueError("Emoticon Name is not set.")
        
        # if there are no folders which have the same name as the emoticon name, create one
        if not os.path.exists(self.title):
            os.makedirs(self.title)
        self.download_thumnail()
        self.download_emoticon(id=self.id)

    def download_thumnail(self):
        cover_url = f"https://item.kakaocdn.net/dw/{self.id}.gift.jpg"
        headers = {"User-Agent": "Android"}
        response = requests.get(cover_url, headers=headers)
        
        if response.status_code == 200:
            with open(f"{self.title}/{self.id}_cover.jpg", "wb+") as f:
                f.write(response.content)
        else:
            raise RuntimeError("Failed to download the cover image.")

    def download_emoticon(self, id):
        play_exts = [".webp", ".gif", ".png", ""]
        play_types = ["emot", "emoji", ""]  # emot = normal; emoji = mini

        # 1. Check the valid extension and type of the sticker
        headers = {"User-Agent": "Android"}
        play_type = ""
        play_ext = ""
        for play_type, play_ext in itertools.product(play_types, play_exts):
                r = requests.get(
                    f"https://item.kakaocdn.net/dw/{id}.{play_type}_001{play_ext}",
                    headers=headers,
                )
                if r.ok:
                    print(f"Found Play type: {play_type}, Play Ext: {play_ext}")
                    break
        if play_ext == "":
            raise ValueError(f"Failed to determine extension of {id}")

        # 2. Download the stickers
        targets: list[tuple[str, Path]] = []
        for cnt in range(1, self.stickers_count + 1):
            file_url = f"https://item.kakaocdn.net/dw/{id}.{play_type}_{cnt:03d}{play_ext}"
            file_dl_path = Path(f"{self.title}/{id}_{cnt:03d}{play_ext}")
            targets.append((file_url, file_dl_path))

            # Download
            print(f"Downloading {file_url}...")
            retries = 3
            for retry in range(retries):
                try:
                    response = requests.get(file_url, headers=headers, stream=True, allow_redirects=True)
                    # print response code
                    if not response.ok:
                        print(f"{file_url} not responding...")
                    else:
                        with open(file_dl_path, "wb+") as f:
                            f.write(response.content)
                        print("Downloaded.")
                        break

                except requests.exceptions.RequestException as e:
                    print(f"Cannot download {file_url} (tried {retry+1}/{retries} times): {e}")

        # 3. Decrypt the animated stickers if necessary
        for target in targets:
            f_path = target[1]
            ext = Path(f_path).suffix

            if ext not in (".gif", ".webp"):
                continue

            with open(f_path, "rb") as f:
                data = f.read()
            data = decrypt.xor_data(data)
            print(f"Decrypted {f_path}")
            with open(f_path, "wb") as f:
                f.write(data)

            # Convert Webp into apng file (For Signal Messenger)
            if ext == ".webp":
                self.convert_webp_to_apng(f_path)

        print(f"Finished getting {self.title} stickers.")