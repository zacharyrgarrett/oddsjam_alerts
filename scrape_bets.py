# Scrape Oddsjam bets and send Discord alerts
import math
import time
import datetime
from multiprocessing.pool import ThreadPool
from discord import Webhook, RequestsWebhookAdapter
from selenium import webdriver
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

WEBHOOK_URL = "https://discord.com/api/webhooks/979287046759804998/XZrPYX5opbSd3cA18m2ECNwxgxY53zHTMs2D096BdngFZlCU5YFK4xw9BsZbMcBNtZAi"
OJ_EV_URL = "https://oddsjam.com/betting-tools/positive-ev"

GROWTH_RATE = 0.0233778
INFLECTION_POINT = 103.47824
ASYMPTOTE = 0.6040086

chrome_options = Options()
# chrome_options.add_argument('--headless')
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
        bet_description=5,
        sportsbook_info=6,
        market_odds=7,
        no_vig_odds=8,
        width=9
    )

    def __init__(self, bet_row, minimal_scrape=False):
        start_time = time.time()
        self.bet_id = ''
        self.ev_percent = ''
        self.game_date = ''
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
        self.time_setters = time.time() - start_time

        # Extract attributes
        self.__bet_row_elements = bet_row.find_elements(By.XPATH, "./td")
        self.__get_percent()
        self.width = self.__get_inner_text("width")

        if not minimal_scrape:
            self.gather_rest_information()

    @staticmethod
    def __convert_date(date_string):
        date_string = date_string[:date_string.find(" at ")]
        date_obj = datetime.datetime.strptime(date_string, "%a, %b %d")
        date_obj = date_obj.replace(year=datetime.date.today().year)
        return date_obj.strftime("%#m/%#d/%Y")

    def __get_inner_text(self, element_name):
        return self.__bet_row_elements[self.__element_map[element_name]].get_attribute("innerText")

    def __get_bet_id(self):
        bet_link = self.__bet_row_elements[self.__element_map["bet_id"]].find_element(By.TAG_NAME, "a").get_attribute(
            "href")
        game_string = bet_link.split("/")[5] + self.bet_description + str(self.actual_odds)
        self.bet_id = abs(hash(game_string)) % (10 ** 8)

    def __get_percent(self):
        percents = self.__bet_row_elements[self.__element_map["percent"]].find_elements(By.XPATH, "./a/div/span")
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

    def __get_bet_description(self):
        bet_sides = self.__bet_row_elements[self.__element_map["bet_description"]].find_elements(By.TAG_NAME, "p")
        self.bet_description = bet_sides[self.__top_or_bottom_bet].get_attribute("innerText")

    def __get_sportsbook_info(self):
        info = self.__bet_row_elements[self.__element_map["sportsbook_info"]].find_element(By.XPATH, "./span/span")

        # Get Odds
        self.actual_odds = info.find_elements(By.XPATH, "./span")[self.__top_or_bottom_bet].get_attribute("innerText")

        # Get list of books
        image_divs = info.find_elements(By.XPATH, "./div")[self.__top_or_bottom_bet].find_elements(By.XPATH, "./div")
        for image_div in image_divs:
            img_elem = image_div.find_elements(By.XPATH, "./a/span/img")
            if len(img_elem):
                book_name = img_elem[0].get_attribute("alt")
            else:
                book_name = image_div.find_element(By.XPATH, "./span/img").get_attribute("alt")
            if book_name != "OddsJam":
                self.sportsbooks.append(book_name)

    def __get_market_odds(self):
        odds = self.__bet_row_elements[self.__element_map["market_odds"]].find_elements(By.XPATH, "./a/span/span")
        self.market_odds = odds[self.__top_or_bottom_bet].get_attribute("innerText")

    def __get_no_vig_odds(self):
        odds = self.__bet_row_elements[self.__element_map["no_vig_odds"]].find_elements(By.XPATH, "./a/span/span")
        self.no_vig_odds = odds[self.__top_or_bottom_bet].get_attribute("innerText")

    def __get_recommended_bet_size(self):
        bet_midpoint = (self.__max_bet - self.__min_bet) / 2
        odds = int(self.actual_odds)
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
        self.game_date = self.__convert_date(self.__get_inner_text("game_date"))
        self.__get_event_info()
        self.market = self.__get_inner_text("market")
        self.__get_bet_description()
        self.__get_sportsbook_info()
        self.__get_market_odds()
        self.__get_no_vig_odds()
        self.__get_recommended_bet_size()
        self.__get_bet_id()


def send_to_discord(msg):
    webhook = Webhook.from_url(WEBHOOK_URL, adapter=RequestsWebhookAdapter())
    webhook.send(msg)


def desired_bet(width, percent):
    min_percent = ASYMPTOTE / (1 + math.exp((-1 * GROWTH_RATE) * (width - INFLECTION_POINT)))
    return percent >= (min_percent * 100)


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
    for bet_row in bet_rows[1:]:
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


def clean_alert_log():
    print("Cleaning the alert log...")
    date1 = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    for bet_id in alert_log.keys():
        date2 = datetime.datetime.strptime(alert_log[bet_id], "%m/%d/%Y")
        if date2 < date1:
            del (alert_log[bet_id])


def start_scraping():
    new_browser_count = 0
    while True:
        try:
            # Check if alert_log needs to be cleaned
            if new_browser_count > 50:
                clean_alert_log()
                new_browser_count = 0

            # Retrieve page
            new_browser_count += 1
            driver = webdriver.Chrome("drivers/chromedriver_win.exe", desired_capabilities=caps, options=chrome_options)
            print("Driver created...")
            driver.get(OJ_EV_URL)
            print("URL requested...")
            time.sleep(7)

            # Check for message box
            if len(driver.find_elements(By.ID, "intercom-container")) > 0:
                print("Detected intercom-container")
                driver.execute_script("""
                document.getElementById("intercom-container").remove();
                """)
                print("Removed intercom-container")

            # Get refresh button
            print("Retrieving refresh button...")
            button = driver.find_element(By.TAG_NAME, "main").find_element(By.TAG_NAME, "button")
            print("Refresh button retrieved...")
            time.sleep(1)

            # Loop refresh
            refresh_count = 0
            while True:
                refresh_count += 1
                button.click()
                print("Clicked 'Refresh'...")
                time.sleep(3)
                rows = driver.find_element(By.TAG_NAME, "table").find_elements(By.TAG_NAME, "tr")
                print("Starting 'check_bets' operation...")
                start_time = time.time()
                pool = ThreadPool(10)
                pool.map(check_bet, rows[1:])
                pool.close()
                pool.join()
                print(f"Took {time.time() - start_time} seconds to finish...")
                if refresh_count >= 30:
                    raise Exception("Refreshing Browser...")

        except Exception as ex:
            print(ex)
            print("Extraction restarting...")
            driver.quit()
            del driver
            time.sleep(3)


if __name__ == "__main__":
    start_scraping()