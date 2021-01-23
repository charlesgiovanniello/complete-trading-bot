# encoding: utf-8

import logging
import multiprocessing
import os
import threading
from bs4 import BeautifulSoup as soup
from urllib.request import urlopen
from datetime import datetime as dt, time

import gvars
from assetHandler import AssetHandler
from other_functions import *
from stocklib import *
from traderlib import *
import datetime, pytz, holidays

#For checking if market is open
tz = pytz.timezone('US/Eastern')
us_holidays = holidays.US()

# Global object we log to; the handler will work with any log message
_L = logging.getLogger("demo")

# Create a special logger that logs to per-thread-name files
class MultiHandler(logging.Handler):
    def __init__(self, dirname):
        super(MultiHandler, self).__init__()
        self.files = {}
        self.dirname = dirname
        if not os.access(dirname, os.W_OK):
            raise Exception("Directory %s not writeable" % dirname)

    def flush(self):
        self.acquire()
        try:
            for fp in list(self.files.values()):
                fp.flush()
        finally:
            self.release()

    def _get_or_open(self, key):
        # Get the file pointer for the given key, or else open the file e
        self.acquire()
        try:
            if key in self.files:
                return self.files[key]
            else:
                fp = open(os.path.join(self.dirname, "%s.log" % key), "a")
                self.files[key] = fp
                return fp
        finally:
            self.release()

    def emit(self, record):
        # No lock here; following code for StreamHandler and FileHandler
        try:
            fp = self._get_or_open(record.threadName)
            msg = self.format(record)
            fp.write('%s\n' % msg)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            self.handleError(record)

def clean_open_orders(api):
    # First, cancel any existing orders so they don't impact our buying power.
    orders = api.list_orders(status="open")

    print('\nCLEAR ORDERS')
    print('%i orders were found open' % int(len(orders)))

    for order in orders:
      api.cancel_order(order.id)


def clean_positions(api):
    # Sell all positions
    positions = api.list_positions()
    print('\nSELL OPEN POSITIONS')
    #api.cancel_all_orders()
    api.close_all_positions()
    # print(print('%i positions were found' % int(len(positions))))
    # for position in positions:
    #     api.submit_order(position.symbol, position.qty, 'sell', 'market', 'day')
    #     print(position.qty + " shares of " +position.symbol + " sold" )

def afterHours(now = None):
        if not now:
            now = datetime.datetime.now(tz)
        openTime = datetime.time(hour = 9, minute = 30, second = 0)
        closeTime = datetime.time(hour = 16, minute = 0, second = 0)
        # If a holiday
        if now.strftime('%Y-%m-%d') in us_holidays:
            return True
        # If before 0930 or after 1600
        if (now.time() < openTime) or (now.time() > closeTime):
            return True
        # If it's a weekend
        if now.date().weekday() > 4:
            return True

        return False

def check_account_ok(api):

    account = api.get_account()
    if account.account_blocked or account.trading_blocked or account.transfers_blocked:

        print('OJO, account blocked. WTF?')
        import pdb; pdb.set_trace()


def run_tbot(_L,assHand,account):

    # initialize trader object
    trader = Trader(gvars.API_KEY, gvars.API_SECRET_KEY, _L, account)

    while True:

        ticker = assHand.find_target_asset()
        stock = Stock(ticker)

        ticker,lock = trader.run(stock) # run the trading program

        if lock: # if the trend is not favorable, lock it temporarily
            assHand.lock_asset(ticker)
        else:
            assHand.make_asset_available(ticker)

def get_new_assets():
    # Get the HTML data from yahoo finance
    yahoo_url = 'https://finance.yahoo.com/most-active'
    yahoo_data = urlopen(yahoo_url)
    yahoo_html = yahoo_data.read()
    yahoo_data.close()
    page_soup = soup(yahoo_html, 'html.parser')
    table = page_soup.find_all('td', {'aria-label': 'Symbol'})

    # Add to CSV file
    f = open('_raw_assets.csv', 'w')
    for i, each in enumerate(table):
        table1 = each.find("a").text
        f.write(table1)
        if i < (len(table) - 1):
            f.write(',')

    #  financial content website doesnt load data until 10:30 ish. Yahoo finance is better.

    # symbols = []
    # def processPage(page):
    #     url = page
    #     resp = requests.get(url)
    #     html = resp.content
    #     soup = BeautifulSoup(html, 'html.parser')
    #     symbolCol = soup.find_all('td', {'class': 'last col_symbol'})
    #     return [tag.text.strip() for tag in symbolCol]
    #
    # symbols += processPage('http://markets.financialcontent.com/stocks/stocks/dashboard/mostactive')
    # print('run scrape')
    # fObj = open('_raw_assets.csv', 'w')
    # for i, each in enumerate(symbols):
    #     fObj.write(each)
    #     if i < (len(symbols) - 1):
    #         fObj.write(',')
    # fObj.close()
    #new 

def main():

    # Set up a basic stderr logging.
    log_format = '%(asctime)s %(threadName)12s: %(lineno)-4d %(message)s'
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(stderr_handler)

    # Set up a logger that creates one file per thread
    todayLogsPath = create_log_folder(gvars.LOGS_PATH)
    multi_handler = MultiHandler(todayLogsPath)
    multi_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(multi_handler)

    # Set default log level, log a message
    _L.setLevel(logging.DEBUG)
    _L.info("\n\n\nRun initiated")
    _L.info('Max workers allowed: ' + str(gvars.MAX_WORKERS))

    # initialize the API with Alpaca
    api = tradeapi.REST(gvars.API_KEY, gvars.API_SECRET_KEY, gvars.ALPACA_API_URL, api_version='v2')

    # Find the assets we are trading today
    get_new_assets()

    # initialize the asset handler
    assHand = AssetHandler()

    # get the Alpaca account ready
    try:
        _L.info("Getting account")
        check_account_ok(api) # check if it is ok to trade
        account = api.get_account()
        clean_open_orders(api) # clean all the open orders
        clean_positions(api)
        _L.info("Got it")
    except Exception as e:
        _L.info(str(e))

    for thread in range(gvars.MAX_WORKERS): # this will launch the threads
        worker = 'th' + str(thread) # establishing each worker name

        worker = threading.Thread(name=worker,target=run_tbot,args=(_L,assHand,account))
        worker.start() # it runs a run_tbot function, declared here as well

        time.sleep(1)


if __name__ == '__main__':
    api = tradeapi.REST(gvars.API_KEY, gvars.API_SECRET_KEY, gvars.ALPACA_API_URL, api_version='v2')
    # These will be our trading window bounds. We give margins to allow ticker data to come in, and to ensure we can close positions before market closes.
    day_start = dt(dt.now().year, dt.now().month, dt.now().day, 9, 52)
    day_end = dt(dt.now().year, dt.now().month, dt.now().day, 15, 58)
    while True:
        
        p = multiprocessing.Process(target=main)
        if day_start < dt.now() < day_end:
            p.start()

        # Trading window is open, constantly ensure that is the case. If not, continue on .
        while (day_start < dt.now() < day_end) and not afterHours():
            time.sleep(1)
        
        # At this point, trading window is closed and we kill the trader, clean out open positions.
        while not ( (day_start < dt.now() < day_end) and not afterHours() ):
            if p.is_alive():
                p.terminate()
                p.join()
                clean_positions(api)
                print("Positions cleared. Market closing shortly...")
                time.sleep(10)
            if afterHours():
                print("Trading window is closed, afterHours = " + str(afterHours()))
            if not afterHours():
                #Set new day start / end time, display countdown.
                day_start = dt(dt.now().year, dt.now().month, dt.now().day, 9, 52)
                day_end = dt(dt.now().year, dt.now().month, dt.now().day, 15, 58)
                print("Market is open. Gathering assets. Trading will commence in: " + str( day_start - dt.now())) 
            # Neat lil dot animation
            for i in range(10):
                print('.')
                time.sleep(1)







                