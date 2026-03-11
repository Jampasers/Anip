# Growtopia Social Login Script

This script automates login to Growtopia using Google or Apple accounts with Selenium and undetected-chromedriver. It extracts the token after authentication for further processing.

## Features

- Automated Google login
- Automated Apple login
- CAPTCHA handling with CapSolver extension (Google flow)
- Token extraction for post-login processing
- Proxy support

## Requirements

- Python 3.x
- [Selenium](https://pypi.org/project/selenium/)
- [undetected-chromedriver](https://pypi.org/project/undetected-chromedriver/)
- [fake-useragent](https://pypi.org/project/fake-useragent/)
- [requests](https://pypi.org/project/requests/)
- [bs4](https://pypi.org/project/beautifulsoup4/)

## Installation

1. Clone the repository:
    ```sh
    git clone https://github.com/BarisSenel/selenium-growtopia-login.git
    cd selenium-growtopia-login
    ```

2. Install the required Python packages:
    ```sh
    pip install selenium undetected-chromedriver fake-useragent requests beautifulsoup4
    ```

3. Download the CapSolver extension and place it in the project directory. Ensure the folder name is `Capsolver`.

## Usage

Run Google login:
```sh
python getToken.py -provider google -mail your-email@gmail.com -password your-password -recoverymail your-recovery-email -proxy socks5://your-proxy
```

Run Apple login:
```sh
python getToken.py -provider apple -mail your-apple-id@example.com -password your-password -proxy socks5://your-proxy -chromemajor 144
```

Notes:
- `-provider` defaults to `google`.
- `-recoverymail` is optional and only used for Google recovery challenge.
- `-chromemajor` defaults to `144` (pinned Chrome major for faster/stable startup).
- Apple flow: script tries `email -> password -> sign in -> trust device`, then waits redirect back to Growtopia for token extraction.
