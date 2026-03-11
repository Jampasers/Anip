import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import random
import string
import urllib3
import re
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def random_int(min, max):
    return random.randint(min, max)

def hex_string(value):
    return "{:02X}".format(value)

def generate_random_mac_address():
    chars = "0123456789ABCDEF"
    mac = [hex_string(random_int(0, 255)) for _ in range(6)]
    return ":".join(mac)

def generate_rid():
    rid = "".join([hex_string(random_int(0, 255)) for _ in range(16)])
    return rid

def generate_random_number(length):
    first_digit = random.randint(1, 9)
    other_digits = [random.randint(0, 9) for _ in range(length - 1)]
    random_number = ''.join(map(str, [first_digit] + other_digits))
    return random_number

def generate_random_hex(length):
    hex_characters = string.hexdigits[:-6]
    random_hex = ''.join(random.choice(hex_characters) for _ in range(length))
    return random_hex.upper()


def get_meta():
    url = "https://www.growtopia1.com/growtopia/server_data.php"
    headers = {
        "Host": "www.growtopia1.com",
        "User-Agent": "UbiServices_SDK_2022.Release.9_PC64_ansi_static",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
        "Content-Length": "36"
    }
    data = "version=5.11&platform=0&protocol=216"

    response = requests.post(url, headers=headers, data=data, verify=False)
    if response.status_code == 200:
        content = response.content.decode('utf-8')
        # Use regex to find the meta value
        match = re.search(r'meta\|([^ \n\r]+)', content)
        if match:
            return match.group(1)
        else:
            return "Meta value not found"
    else:
        return "Failed to retrieve data"


country_codes = [
    'ad', 'ae', 'af', 'ag', 'ai', 'al', 'am', 'an', 'ao', 'ar', 'as', 'at', 'au', 'aw', 'ax', 'az', 'ba', 'bb', 'bd', 'be', 'bf',
    'bg', 'bh', 'bi', 'bj', 'bm', 'bn', 'bo', 'br', 'bs', 'bt', 'bv', 'bw', 'by', 'bz', 'ca', 'cc', 'cd', 'cf', 'cg', 'ch', 'ci',
    'ck', 'cl', 'cm', 'cn', 'co', 'cr', 'cs', 'cu', 'cv', 'cx', 'cy', 'cz', 'de', 'dj', 'dk', 'dm', 'do', 'dz', 'ec', 'ee', 'eg',
    'eh', 'er', 'es', 'et', 'fi', 'fj', 'fk', 'fm', 'fo', 'fr', 'ga', 'gb', 'gd', 'ge', 'gf', 'gh', 'gi', 'gl', 'gm', 'gn', 'gp',
    'gq', 'gr', 'gs', 'gt', 'gu', 'gw', 'gy', 'ha', 'hk', 'hm', 'hn', 'hr', 'ht', 'hu', 'id', 'ie', 'il', 'in', 'io', 'iq', 'ir',
    'is', 'it', 'jm', 'jo', 'jp', 'ke', 'kg', 'kh', 'ki', 'km', 'kn', 'kp', 'kr', 'kw', 'ky', 'kz', 'la', 'lb', 'lc', 'lg', 'li',
    'lk', 'lr', 'ls', 'lt', 'lu', 'lv', 'ly', 'ma', 'mc', 'md', 'me', 'mg', 'mh', 'mk', 'ml', 'mm', 'mn', 'mo', 'mp', 'mq', 'mr',
    'ms', 'mt', 'mu', 'mv', 'mw', 'mx', 'my', 'mz', 'na', 'nc', 'ne', 'nf', 'ng', 'ni', 'nl', 'no', 'np', 'nr', 'nu', 'nz', 'om',
    'pa', 'pe', 'pf', 'pg', 'ph', 'pk', 'pl', 'pm', 'pn', 'pr', 'ps', 'pt', 'pw', 'py', 'qa', 're', 'ro', 'rs', 'rt', 'ru', 'rw',
    'sa', 'sb', 'sc', 'sd', 'se', 'sg', 'sh', 'si', 'sj', 'sk', 'sl', 'sm', 'sn', 'so', 'sr', 'st', 'sv', 'sy', 'sz', 'tc', 'td',
    'tf', 'tg', 'th', 'tj', 'tk', 'tl', 'tm', 'tn', 'to', 'tr', 'tt', 'tv', 'tw', 'tz', 'ua', 'ug', 'um', 'us', 'uy', 'uz', 'va',
    'vc', 've', 'vg', 'vi', 'vn', 'vu', 'wf', 'ws', 'ye', 'yt', 'za', 'zm', 'zw'
]

special_characters = {
    " ": "",
    "!": "%21",
    "\"": "%22",
    "#": "%23",
    "$": "%24",
    "%": "%25",
    "&": "%26",
    "'": "%27",
    "(": "%28",
    ")": "%29",
    "*": "%2A",
    "+": "%2B",
    ",": "%2C",
    "-": "%2D",
    ".": "%2E",
    "/": "%2F",
    ":": "%3A",
    ";": "%3B",
    "<": "%3C",
    "=": "%3D",
    ">": "%3E",
    "?": "%3F",
    "@": "%40",
    "[": "%5B",
    "\\": "%5C",
    "]": "%5D",
    "^": "%5E",
    "_": "%5F",
    "`": "%60",
    "{": "%7B",
    "|": "%7C",
    "}": "%7D",
    "~": "%7E",
    "\n": "%0A"
}

def percent_encode(return_details=False):
    meta = get_meta()
    print(meta)
    rid_value = generate_rid()
    mac_value = generate_random_mac_address()
    wk_value = generate_random_hex(32)
    requested_name_value = generate_random_number(5)
    country_value = random.choice(country_codes)
    hash2_value = f"-{generate_random_number(9)}"
    hash_value = f"-{generate_random_number(10)}"
    input_string = f"""
        tankIDName|
        tankIDPass|
        requestedName|{requested_name_value}
        f|1
        protocol|216
        game_version|5.11
        fz|22745624
        lmode|0
        cbits|1024
        player_age|22
        GDPR|2
        category|_-5000
        totalPlaytime|0
        klv|d5b6a4db4c447f27ea82d5b1f88bee159346933659f635be5fe0b028c53408af
        hash2|{hash2_value}
        meta|{meta}
        fhash|-716928004
        rid|{rid_value}
        platformID|0,1,1
        deviceVersion|0
        country|{country_value}
        hash|{hash_value}
        mac|{mac_value}
        wk|{wk_value}
        zf|283949556
    """.strip()
    encoded_string = ""
    for char in input_string:
        if char in special_characters:
            encoded_string += special_characters[char]
        else:
            encoded_string += char

    if return_details:
        details = {
            "mac": mac_value,
            "rid": rid_value,
            "wk": wk_value,
            "requested_name": requested_name_value,
            "country": country_value,
            "hash": hash_value,
            "hash2": hash2_value,
            "meta": meta,
        }
        return encoded_string, details

    return encoded_string

def find_provider_link(soup, keywords):
    candidate_groups = [
        soup.find_all('a', {'class': 'btn btn-block', 'href': True}),
        soup.find_all('a', href=True),
    ]

    for anchors in candidate_groups:
        for anchor in anchors:
            href = anchor.get('href')
            if not href:
                continue
            href_lower = href.lower()
            if any(keyword in href_lower for keyword in keywords):
                return href
    return None

def derive_apple_from_google_link(google_link):
    if not google_link:
        return None
    if "/google/redirect" in google_link:
        return google_link.replace("/google/redirect", "/apple/redirect", 1)
    if "/google/" in google_link:
        return google_link.replace("/google/", "/apple/", 1)
    return None

def getUrl(post_body, provider="google"):
    provider = (provider or "google").lower()
    provider_keywords = {
        "google": ("google",),
        "apple": ("apple", "appleid"),
    }
    if provider not in provider_keywords:
        raise ValueError(f"Unsupported provider: {provider}")

    url = "https://login.growtopiagame.com/player/login/dashboard?valKey=40db4045f2d8c572efe8c4a060605726"
    user_agent = UserAgent().chrome
    headers = {
        "Host": "login.growtopiagame.com",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Microsoft Edge";v="128", "Microsoft Edge WebView2";v="128"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "Windows",
        "Content-Type": "application/x-www-form-urlencoded",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": user_agent,       
        "Origin": "null",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Content-Length": "712"
    }


    session = requests.Session()
    session.verify = False
    response = session.post(url, data=post_body, headers=headers)
    soup = BeautifulSoup(response.content, 'html.parser')
    provider_link = find_provider_link(soup, provider_keywords[provider])

    if provider == "apple" and not provider_link:
        google_link = find_provider_link(soup, provider_keywords["google"])
        provider_link = derive_apple_from_google_link(google_link)

    return provider_link
