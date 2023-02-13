# Scrape Oddsjam bets and send Discord alerts
import math
import time
import datetime
from multiprocessing.pool import ThreadPool

import selenium.common.exceptions
from discord import Webhook, RequestsWebhookAdapter
from selenium import webdriver
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

from concurrent.futures import ProcessPoolExecutor
import multiprocessing

WEBHOOK_URL = "https://discord.com/api/webhooks/<DISCORD_WEBHOOK_KEY>"
OJ_EV_URL = "https://oddsjam.com/betting-tools/positive-ev"

GROWTH_RATE = 0.0233778
INFLECTION_POINT = 103.47824
ASYMPTOTE = 0.6040086
MIN_ACCEPTABLE_PERCENT = 0
previous_date = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
refresh_count = 0

chrome_options = Options()
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument('user-data-dir=C:\\Users\\Zachary\\AppData\\Local\\Google\\Chrome\\User Data')
# chrome_options.add_argument('window-size=1920x1920')
# chrome_options.add_argument("start-maximized")
# chrome_options.add_argument("enable-automation")
# chrome_options.add_argument("--disable-browser-side-navigation")
# chrome_options.add_argument("--disable-gpu")

caps = DesiredCapabilities.CHROME
caps["pageLoadStrategy"] = "none"
caps["applicationCacheEnabled"] = False
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36'
}

# Keeps track of already alerted bets
alert_log = dict()


class Bet:
    __bet_row_elements = []
    __bet_info_elements = []
    __top_or_bottom_bet = 0
    __min_odds = -300
    __max_odds = 300
    __min_bet = 10
    __max_bet = 20
    __element_map = dict(
        bet_id=0,
        percent=1,
        game_date=2,
        event_info=3,
        market=4,
        bet_info=5,
        width=6
    )
    __bet_element_map = dict(
        bet_description=0,
        book_info=1,
        market_info=2,
        no_vig_odds=3
    )

    def __init__(self, bet_row, minimal_scrape=False):
        self.oj_bet_url = ''
        self.bet_id = ''
        self.ev_percent = ''
        self.game_date = ''
        self.game_time = ''
        self.matchup = ''
        self.sport = ''
        self.market = ''
        self.bet_description = ''
        self.sportsbooks = []
        self.actual_odds = ''
        self.market_odds = ''
        self.no_vig_odds = ''
        self.width = ''
        self.recommended_bet = 0

        # Extract attributes
        self.oj_bet_url = bet_row.get_attribute("href")
        self.__bet_row_elements = bet_row.find_elements(By.XPATH, "./div")
        self.__get_percent()
        self.width = self.__get_inner_text("width")

        if not minimal_scrape:
            self.gather_rest_information()

    @staticmethod
    def __convert_date(date_string):
        date_obj = datetime.datetime.strptime(date_string, "%a, %b %d at %I:%M %p")
        today = datetime.date.today()
        if date_obj.month < today.month:
            date_obj = date_obj.replace(year=today.year + 1)
        else:
            date_obj = date_obj.replace(year=today.year)
        return date_obj.strftime("%#m/%#d/%Y"), date_obj.strftime("%H:%M:%S")

    def __get_inner_text(self, element_name):
        return self.__bet_row_elements[self.__element_map[element_name]].get_attribute("innerText")

    def __get_bet_id(self):
        game_string = self.oj_bet_url.split("/")[5] + self.bet_description + str(self.actual_odds)
        self.bet_id = abs(hash(game_string)) % (10 ** 8)

    def __get_percent(self):
        percents = self.__bet_row_elements[self.__element_map["percent"]].find_elements(By.XPATH, "./div/span")
        top = percents[0].find_element(By.XPATH, "./p").get_attribute("innerText")
        bottom = percents[1].find_element(By.XPATH, "./p").get_attribute("innerText")

        if top == "–":
            self.__top_or_bottom_bet = 1
            self.ev_percent = bottom
        elif bottom == "–":
            self.__top_or_bottom_bet = 0
            self.ev_percent = top
        else:
            if float(top.replace("%", "")) > float(bottom.replace("%", "")):
                self.__top_or_bottom_bet = 0
                self.ev_percent = top
            else:
                self.__top_or_bottom_bet = 1
                self.ev_percent = bottom

    def __get_event_info(self):
        event_info = self.__bet_row_elements[self.__element_map["event_info"]].find_elements(By.TAG_NAME, "p")
        self.matchup = event_info[0].get_attribute("innerText")
        self.sport = event_info[1].get_attribute("innerText")

    def __get_bet_info(self):
        bet_sides = self.__bet_row_elements[self.__element_map["bet_info"]].find_elements(By.XPATH, "./div")
        for div in bet_sides:
            self.__bet_info_elements.append(div.find_elements(By.XPATH, "./div"))
        self.__get_bet_description()
        self.__get_sportsbook_info()
        self.__get_market_odds()
        self.__get_no_vig_odds()
        self.__get_recommended_bet_size()
        self.__get_bet_id()

    def __get_bet_description(self):
        self.bet_description = self.__bet_info_elements[self.__top_or_bottom_bet][self.__bet_element_map["bet_description"]].get_attribute("innerText")

    def __get_sportsbook_info(self):
        info = self.__bet_info_elements[self.__top_or_bottom_bet][self.__bet_element_map["book_info"]].find_element(By.XPATH, "./div")

        # Get Odds
        self.actual_odds = info.find_element(By.XPATH, "./span").get_attribute("innerText")

        # Get list of books
        image_divs = info.find_elements(By.XPATH, "./div/div")

        for image in image_divs:
            book = image.find_elements(By.XPATH, "./img")
            if not len(book):
                book = image.find_elements(By.XPATH, "./a/img")
            book_name = book[0].get_attribute("alt")
            if book_name != "OddsJam" and book_name not in self.sportsbooks:
                self.sportsbooks.append(book_name)

    def __get_market_odds(self):
        odds = self.__bet_info_elements[self.__top_or_bottom_bet][self.__bet_element_map["market_info"]].find_element(By.XPATH, "./div/span")
        self.market_odds = odds.get_attribute("innerText")

    def __get_no_vig_odds(self):
        odds = self.__bet_info_elements[self.__top_or_bottom_bet][self.__bet_element_map["no_vig_odds"]].find_element(By.XPATH, "./div/span")
        self.no_vig_odds = odds.get_attribute("innerText")

    def __get_recommended_bet_size(self):
        bet_midpoint = (self.__max_bet - self.__min_bet) / 2
        odds = int(self.market_odds)
        bet_size = 0
        if odds < 0:
            slope = bet_midpoint / (self.__min_odds + 100)
            bet_size = self.__min_bet + abs(slope * (odds - self.__max_odds)) - bet_midpoint
        else:
            slope = bet_midpoint / (self.__max_odds - 100)
            bet_size = slope * (self.__max_odds - odds) + self.__min_bet
        self.recommended_bet = round(bet_size, 2)

    def msg(self):
        return "\n".join([
            "------------------------------------------------------",
            f"Bet: {self.bet_description} {self.market}",
            f"Event: {self.matchup} - {self.game_date}",
            f"Amount: ${self.recommended_bet}",
            f"Sportsbooks: {','.join(self.sportsbooks)}",
            f"Odds: {self.actual_odds}",
            f"+EV Percent: {self.ev_percent}",
            f"Width: {self.width}",
            "------------------------------------------------------",
            ",".join([
                self.game_date, "Name", ";".join(self.sportsbooks),
                self.matchup + " " + self.bet_description + " " + self.market,
                "", str(int(self.actual_odds)), str(int(self.market_odds)), "$" + str(self.recommended_bet),
                self.ev_percent
            ]),
            "------------------------------------------------------"
        ])

    def gather_rest_information(self):
        self.game_date, self.game_time = self.__convert_date(self.__get_inner_text("game_date"))
        self.__get_event_info()
        self.market = self.__get_inner_text("market")
        self.__get_bet_info()


def send_to_discord(msg):
    webhook = Webhook.from_url(WEBHOOK_URL, adapter=RequestsWebhookAdapter())
    webhook.send(msg)


def calculate_acceptable_percent(width):
    return (ASYMPTOTE / (1 + math.exp((-1 * GROWTH_RATE) * (width - INFLECTION_POINT)))) * 100


def desired_bet(width, percent):
    min_percent = calculate_acceptable_percent(width)
    return percent >= min_percent


def check_bet(bet_row):
    bet = Bet(bet_row, minimal_scrape=True)
    width = int(bet.width)
    percent = float(bet.ev_percent.replace("%", ""))
    desired = desired_bet(width, percent)

    # Check only percent/width, then decide to gather the rest of the information
    if desired:
        bet.gather_rest_information()
        if bet.bet_id not in alert_log:
            alert_log[bet.bet_id] = bet.game_date
            bet_msg = bet.msg()
            send_to_discord(bet_msg)
            print(f"Alerted bet! Details below:\n{bet_msg}\n")


def check_bets(bet_rows):
    for bet_row in bet_rows:
        bet = Bet(bet_row, minimal_scrape=True)

        width = int(bet.width)
        percent = float(bet.ev_percent.replace("%", ""))

        # Break loop to save processing time
        if percent < MIN_ACCEPTABLE_PERCENT:
            break

        # Check only percent/width, then decide to gather the rest of the information
        if desired_bet(width, percent):
            bet.gather_rest_information()
            if bet.bet_id not in alert_log:
                alert_log[bet.bet_id] = bet.game_date
                bet_msg = bet.msg()
                send_to_discord(bet_msg)
                print(f"Alerted bet! Details below:\n{bet_msg}\n")

        # Free memory
        del bet


def clean_alert_log():
    global previous_date
    today = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    if previous_date < today:
        print("Cleaning the alert log...")
        for bet_id in alert_log.keys():
            bet_date = datetime.datetime.strptime(alert_log[bet_id], "%m/%d/%Y")
            if bet_date < today:
                del (alert_log[bet_id])
        previous_date = today


def get_clear_browsing_button(driver):
    """Find the "CLEAR BROWSING BUTTON" on the Chrome settings page."""
    # return driver.find_element(By.CSS_SELECTOR, '* /deep/ #clearBrowsingDataConfirm')
    return driver.execute_script(
        "return document.querySelector('settings-ui').shadowRoot.querySelector('settings-main').shadowRoot.querySelector('settings-basic-page').shadowRoot.querySelector('settings-section > settings-privacy-page').shadowRoot.querySelector('settings-clear-browsing-data-dialog').shadowRoot.querySelector('#clearBrowsingDataDialog').querySelector('#clearBrowsingDataConfirm')")


def clear_cache(driver, timeout=60):
    """Clear the cookies and cache for the ChromeDriver instance."""
    # navigate to the settings page
    bet_window = driver.current_window_handle
    driver.switch_to.new_window("tab")
    driver.get('chrome://settings/clearBrowserData')

    # click the button to clear the cache
    time.sleep(2)
    get_clear_browsing_button(driver).click()
    print("Successfully cleared cache...")
    time.sleep(2)

    # close tab
    driver.close()
    driver.switch_to.window(bet_window)


def get_oj_url(driver):
    driver.get(OJ_EV_URL)
    driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
    time.sleep(7)


def make_oj_driver(driver):
    if driver is not None:
        """Clear Cache"""
        clear_cache(driver)
    else:
        """Create Driver"""
        while driver is None:
            try:
                time.sleep(5)
                driver = webdriver.Chrome("drivers/chromedriver_win.exe", desired_capabilities=caps,
                                          options=chrome_options)
                print("Driver created...")
            except selenium.common.exceptions.InvalidArgumentException as ex:
                print("Wasn't able to use the chrome user profile, trying again in 30 seconds...")

    get_oj_url(driver)

    """Check for OJ message boxes"""
    check_for_message_boxes(driver)

    return driver, get_refresh_button(driver)


def check_for_message_boxes(driver):
    if len(driver.find_elements(By.ID, "intercom-container")) > 0:
        print("Detected intercom-container")
        driver.execute_script("""
        document.getElementById("intercom-container").remove();
        """)
        print("Removed intercom-container")


def get_refresh_button(driver):
    print("Retrieving refresh button...")
    refresh_button = driver.find_element(By.TAG_NAME, "main").find_element(By.TAG_NAME, "button")
    print("Refresh button retrieved...")
    return refresh_button


def refresh_table(refresh_button):
    global refresh_count
    refresh_count += 1
    if refresh_button is None:
        print("Could not refresh table...")
    else:
        refresh_button.click()
        print("Clicked 'Refresh'...")
        time.sleep(3)


def read_new_bets(driver):
    rows = driver.find_element(By.CLASS_NAME, "overflow-scroll").find_elements(By.XPATH, "./a")
    print("Starting 'check_bets' operation...")
    start_time = time.time()

    # Threading
    # pool = ThreadPool(10)
    # pool.map(check_bet, rows[1:])
    # pool.close()
    # pool.join()

    # Multiprocessing attempt #1
    # with ProcessPoolExecutor() as executor:
    #     for bet_row in rows[1:]:
    #         executor.submit(check_bet, bet_row)

    # MP attempt #2
    processes = []
    n_processes = 10
    process_size = math.ceil(len(rows) / n_processes)
    for i in range(1, n_processes + 1):
        start = (i - 1) * process_size
        end = i * process_size if len(rows) >= i * process_size else len(rows)
        p = multiprocessing.Process(target=check_bets(rows[start:end]))
        p.start()
        processes.append(p)
    for p in processes:
        p.join()

    print(f"Took {time.time() - start_time} seconds to finish...")


def check_for_clear_cache(driver, refresh_button):
    global refresh_count
    if refresh_count > 25:
        driver, refresh_button = make_oj_driver(driver)
        refresh_count = 0
    return driver, refresh_button


def set_min_acceptable_percent():
    global MIN_ACCEPTABLE_PERCENT
    MIN_ACCEPTABLE_PERCENT = calculate_acceptable_percent(0)


def start_scraping():
    driver = None
    set_min_acceptable_percent()
    while True:
        try:
            # Retrieve page
            driver, refresh_button = make_oj_driver(driver)

            # Loop refresh
            while True:
                clean_alert_log()
                refresh_table(refresh_button)
                read_new_bets(driver)
                driver, refresh_button = check_for_clear_cache(driver, refresh_button)
        except Exception as ex:
            print("Error:")
            print(ex)


if __name__ == "__main__":
    start_scraping()
