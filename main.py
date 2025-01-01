from download_emoticon import KakaoEmoticonDownloader

url = input(
    "카카오톡 스마트폰 앱의 이모티콘 공유 링크를 입력해주세요. \n"
    "https://emoticon.kakao.com/items/(영숫자 혼합 ID)?lang=ko&referer=share_link과 같은 형태여야 합니다. \n"
    "URL: "
)

downloader = KakaoEmoticonDownloader()
downloader.get_info_from_share_link(url)
downloader.download_stickers()