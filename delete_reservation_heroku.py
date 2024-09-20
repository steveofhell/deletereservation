import requests
import logging
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
from bs4 import BeautifulSoup
import time
import os
import json
import random

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Google Sheets setup

SERVICE_ACCOUNT_INFO = os.environ.get('SERVICE_ACCOUNT_INFO', 'service_account.json')

with open(SERVICE_ACCOUNT_INFO, 'r') as f:
    SERVICE_ACCOUNT_INFO = json.load(f)

SHEET_ID = os.environ.get('SHEET_ID')
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=scope)
client = gspread.authorize(creds)

google_creds = os.environ.get('GOOGLE_CREDENTIALS')
if google_creds:
    with open('google_credentials.json', 'w') as f:
        f.write(google_creds)

# Define BASE_URL and LOGIN_URL
BASE_URL = "https://picklesocialclub.playbypoint.com"
LOGIN_URL = f"{BASE_URL}/users/sign_in"

# List of user agents for rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
]

def get_random_user_agent():
    return random.choice(USER_AGENTS)


def get_sheet():
    try:
        return client.open_by_key(SHEET_ID).worksheet('Overview'), client.open_by_key(SHEET_ID).worksheet('id')
    except gspread.exceptions.APIError as e:
        logging.error(f"API Error: {str(e)}")
    except gspread.exceptions.WorksheetNotFound:
        logging.error("Worksheet 'Overview' not found in the specified sheet.")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {str(e)}")
    return None


def get_accounts(id_sheet):
    accounts = []
    for row in id_sheet.get_all_values()[1:]:  # Skip header row
        if len(row) >= 4:
            accounts.append({
                'email': row[0],
                'password': row[1],
                'id': row[2],
                'name': row[3]
            })
    logging.info(f"Retrieved {len(accounts)} accounts from the sheet")
    return accounts


def get_headers():
    return {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }


def get_csrf_token(session, url):
    logging.info(f"Attempting to get CSRF token from URL: {url}")
    try:
        headers = get_headers()
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        time.sleep(random.uniform(1, 3))  # Random delay between 1 and 3 seconds
        return get_csrf_token_from_response(response)
    except requests.RequestException as e:
        logging.error(f"Failed to get CSRF token from {url}: {e}")
        raise


def get_csrf_token_from_response(response):
    try:
        soup = BeautifulSoup(response.text, 'html.parser')
        csrf_token = soup.find('meta', attrs={'name': 'csrf-token'})
        if csrf_token and 'content' in csrf_token.attrs:
            token = csrf_token['content']
            logging.info("Successfully extracted CSRF token")
            return token
        else:
            logging.error("CSRF token not found in the response")
            return None
    except Exception as e:
        logging.error(f"Failed to extract CSRF token from response: {e}")
        return None


def refresh_token(session):
    logging.info("Refreshing CSRF token")
    try:
        response = session.get(f"{BASE_URL}/account/reservations")
        return get_csrf_token_from_response(response)
    except Exception as e:
        logging.error(f"Failed to refresh CSRF token: {e}")
        return None


def check_authentication(session):
    logging.info("Checking authentication status")
    try:
        response = session.get(f"{BASE_URL}/account/reservations", allow_redirects=False)
        logging.info(f"Authentication check status code: {response.status_code}")
        if response.status_code == 200:
            logging.info("Authentication is still valid")
            return True
        else:
            logging.warning(f"Authentication has expired. Redirected to: {response.headers.get('Location', 'Unknown')}")
            return False
    except Exception as e:
        logging.error(f"Error checking authentication: {e}")
        return False


def login(session, email, password):
    logging.info(f"Attempting login for email: {email}")
    try:
        csrf_token = get_csrf_token(session, LOGIN_URL)
        if not csrf_token:
            raise Exception("Failed to get CSRF token for login")

        login_data = {
            'user[email]': email,
            'user[password]': password,
            'authenticity_token': csrf_token
        }
        headers = get_headers()
        headers.update({
            'Referer': LOGIN_URL,
            'Origin': BASE_URL,
            'Content-Type': 'application/x-www-form-urlencoded',
        })

        time.sleep(random.uniform(2, 4))  # Random delay before login attempt

        response = session.post(LOGIN_URL, data=login_data, headers=headers, allow_redirects=False)
        if response.status_code == 302:  # Successful login usually redirects
            logging.info(f"Login successful for email: {email}")
            return csrf_token
        else:
            logging.error(f"Login failed for email: {email}. Status code: {response.status_code}")
            logging.error(f"Response content: {response.text}")
            raise Exception(f"Login failed for email: {email}")
    except Exception as e:
        logging.error(f"Login failed for email {email}: {e}")
        raise


def get_reservations_to_delete(sheet_data, account_name):
    tomorrow = (datetime.now().date() + timedelta(days=1)).strftime('%Y-%m-%d')
    logging.info(f"Checking for reservations on date: {tomorrow} for account: {account_name}")
    logging.info(f"Total rows in sheet_data: {len(sheet_data)}")

    reservations_to_delete = []

    for i, row in enumerate(sheet_data[1:], start=2):
        logging.info(f"Checking row {i}: {row}")

        if len(row) <= 1:
            logging.warning(f"Row {i} has insufficient data: {row}")
            continue

        reservation_date = row[1] if len(row) > 1 else "No date"
        column_g_value = row[6] if len(row) > 6 else "No value"
        column_e_value = row[5] if len(row) > 5 else "No value"

        logging.info(f"Row {i}: Date: {reservation_date}, Column G: {column_g_value}, Column E: {column_e_value}")

        if reservation_date == tomorrow:
            logging.info(f"Found matching date for row {i}")
            if column_g_value.lower() != 'yes' and column_e_value == account_name:
                logging.info(f"Found reservation to delete in row {i}")
                reservations_to_delete.append({
                    'id': row[0],
                    'date': row[1],
                    'row_index': i
                })
            else:
                logging.info(
                    f"Row {i} does not meet deletion criteria. Column G: {column_g_value}, Column E: {column_e_value}")
        else:
            logging.info(f"Date in row {i} does not match target date {tomorrow}")

    logging.info(f"Found {len(reservations_to_delete)} reservations to potentially delete for account: {account_name}")
    for res in reservations_to_delete:
        logging.info(f"Reservation to delete: ID {res['id']}, Date {res['date']}, Row {res['row_index']}")

    return reservations_to_delete


def delete_sheet_rows(sheet, row_indices):
    if not row_indices:
        return True
    try:
        sheet.delete_rows(min(row_indices), max(row_indices))
        logging.info(f"Successfully deleted rows {row_indices} from the Google Sheet")
        return True
    except Exception as e:
        logging.error(f"Failed to delete rows {row_indices} from the Google Sheet: {e}")
        return False


def delete_reservation(session, csrf_token, reservation_id, email, password, retry_count=0):
    if retry_count >= 3:
        logging.error(f"Max retries reached for deleting reservation {reservation_id}")
        return False

    delete_url = f"{BASE_URL}/api/reservations/{reservation_id}"
    headers = {
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'en-US,en;q=0.9',
        'Origin': BASE_URL,
        'Referer': f'{BASE_URL}/account/reservations',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'User-Agent': get_random_user_agent(),
        'X-CSRF-Token': csrf_token,
        'X-Requested-With': 'XMLHttpRequest'
    }

    try:
        response = session.delete(delete_url, headers=headers, timeout=30)
        logging.info(f"Delete request sent for reservation {reservation_id}. Status code: {response.status_code}")
        logging.info(f"Response content: {response.text}")

        if response.status_code in [200, 204]:
            logging.info(f"Successfully deleted reservation {reservation_id}")
            return True
        elif response.status_code == 401:
            logging.warning("Authentication failed. Attempting to refresh token and re-authenticate.")
            if check_authentication(session):
                new_csrf_token = refresh_token(session)
                if new_csrf_token:
                    time.sleep(2)  # Add a small delay before retrying
                    return delete_reservation(session, new_csrf_token, reservation_id, email, password, retry_count + 1)
            else:
                logging.info("Re-authenticating...")
                new_csrf_token = login(session, email, password)
                time.sleep(2)  # Add a small delay before retrying
                return delete_reservation(session, new_csrf_token, reservation_id, email, password, retry_count + 1)
        else:
            logging.error(f"Failed to delete reservation {reservation_id}. Status code: {response.status_code}")
            logging.error(f"Response content: {response.text}")
            return False

    except requests.RequestException as e:
        logging.error(f"Error while deleting reservation {reservation_id}: {e}")
        time.sleep(5)  # Add a longer delay before retrying after an exception
        return delete_reservation(session, csrf_token, reservation_id, email, password, retry_count + 1)


def process_account(account, overview_sheet):
    logging.info(f"Processing account: {account['name']}")
    session = requests.Session()

    try:

        sheet_data = overview_sheet.get_all_values()
        reservations_to_delete = get_reservations_to_delete(sheet_data, account['name'])

        if not reservations_to_delete:
            logging.info(f"No reservations to delete for account: {account['name']}")
            return

        csrf_token = login(session, account['email'], account['password'])

        deleted_rows = []
        for reservation in reservations_to_delete:
            success = delete_reservation(session, csrf_token, reservation['id'], account['email'], account['password'])
            if success:
                deleted_rows.append(reservation['row_index'])
            else:
                logging.error(f"Failed to delete reservation {reservation['id']} for account: {account['name']}")

        if deleted_rows:
            if delete_sheet_rows(overview_sheet, deleted_rows):
                logging.info(f"Deleted corresponding rows from the Google Sheet for account: {account['name']}")
            else:
                logging.error(
                    f"Failed to delete corresponding rows from the Google Sheet for account: {account['name']}")

        logging.info(f"Reservation deletion process completed for account: {account['name']}")

    except Exception as e:
        logging.error(f"An error occurred while processing account {account['name']}: {e}")
        logging.exception("Exception details:")


def main():
    try:
        overview_sheet, id_sheet = get_sheet()
        accounts = get_accounts(id_sheet)

        for account in accounts:
            logging.info(f"Starting process for account: {account['name']}")
            process_account(account, overview_sheet)
            logging.info(f"Finished processing account: {account['name']}")

        logging.info("All accounts processed")

    except Exception as e:
        logging.error(f"An error occurred in the main function: {e}")
        logging.exception("Exception details:")


if __name__ == "__main__":
    main()