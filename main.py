from serpapi import GoogleSearch
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime, timedelta
import csv
import os
import requests
import openai
import threading

list_states = {
    "AK": "Alaska", "AL": "Alabama", "AR": "Arkansas", "AZ": "Arizona",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "IA": "Iowa", "ID": "Idaho", "IL": "Illinois","IN": "Indiana",
    "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana",
    "MA": "Massachusetts", "MD": "Maryland", "ME": "Maine", "MI": "Michigan", "MN": "Minnesota", "MO": "Missouri", "MS": "Mississippi", "MT": "Montana",
    "NC": "North Carolina", "ND": "North Dakota", "NE": "Nebraska", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NV": "Nevada", "NY": "New York",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah",
    "VA": "Virginia", "VT": "Vermont",
    "WA": "Washington", "WI": "Wisconsin", "WV": "West Virginia", "WY": "Wyoming",
    "DC": "District of Columbia",
    "AS": "American Samoa",
    "GU": "Guam GU",
    "MP": "Northern Mariana Islands",
    "PR": "Puerto Rico PR",
    "VI": "U.S. Virgin Islands",
}

# we hid the api keys, so we need to grab it from the env file
load_dotenv("api_keys.env")
api_key = os.getenv("SERPAPI_GOOGLE_SEARCH_KEY")
open_ai_key = os.getenv("OPEN_AI_KEY")

# helper function to help us calculate date params of the queries
def calc_date(start_date):
    input_date = datetime.strptime(start_date, "%m/%d/%Y")

    # calculations, 2 days before, 3 weeks after
    two_days_before = input_date - timedelta(days=2)
    two_weeks_after = input_date + timedelta(days=14)  
    
    return two_days_before.strftime("%m/%d/%Y"), two_weeks_after.strftime("%m/%d/%Y")


def parse_csv(input_csv):
    columns = ["arrestdate", "CountyName", "ST", "StateCountyFIPS", "FIPSState", "FIPSCounty"]  # columns where we want to select the data from
    data = []
    try:
        with open(input_csv, newline='') as csvfile:
            csv_reader = csv.reader(csvfile)
            header = next(csv_reader)
            
            column_indices = [header.index(col) for col in columns if col in header]
            for row in csv_reader:  # each row with have 3 items: "arrestdate", "CountyName", "ST"
                data.append([row[i] for i in column_indices])
    
    except Exception as e:
        print(f"Error processing CSV file: {e}")  # if the input csv is not valid

    return data


link_attributes = {}  # store the information of duplicated links
manual_check_link_attributes = {}  # store the information of duplicated links that need to be manually checked
# helper function that will do all the searching and writing into the output csv file
def helper(query, county, state, start, end, csv_path1, csv_path2, csv_path3, date, scFIPs, fipsS, fipsC):
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "google_domain": "google.com",
        "gl": "us",
        "hl": "en",
        "tbs": f"cdr:1,cd_min:{start},cd_max:{end}",
        "num": 10,
        "sort": "date"
    }
    try:
        search = GoogleSearch(params)
        # print(search)  # for testing
        results = search.get_dict()
        # print(results)  # for testing
        organic_results = results.get("organic_results", [])
        print(f"Results found: {len(organic_results)}")  # debugging line
        location = f"{county}, {state}"

        for result in organic_results:
            link = result.get("link", "N/A")
            # print(link)
            text = scrape_article_text(link)  # scrape the text of link
            # print(text)

            publish_date = result.get("date", "N/A") 
            publish_year = publish_date[-4:] # grab the year
            # print(list_states[state])
            # print(state)
            # print(link_attributes)

            valid_results = []  # stores the links that passes the chatgpt analysis
            invalid_results = []  # stores the links that does not pass the chatgpt analysis
            manual_check_results = []  # stores the links that needs to be manually checked since paywalls, popups, or other reasons that prevent parsing

            try:
                # Check if text is not None and its length, check if we need to cut down on the text due to gpt api limitations
                if link in manual_check_link_attributes:
                    break
                else:
                    if text == False:  # check if parsing is taking too long
                        print(f"Timeout or error occurred while scraping {link}")
                        if (publish_date != "N/A" and int(publish_year) > 2014) or \
                        (state.lower() in text) or (list_states[state].lower() in text):  # initial date validation and location check
                            manual_check_results.append(result)
                            manual_check_link_attributes[link] = 1

                    elif text is None or len(text) == 0:
                        # Log if no text was scraped
                        print(f"No text found at {link}")
                        text = ""  # Set text to an empty string to handle further processing safely
                        if (publish_date != "N/A" and int(publish_year) > 2014) or \
                        (state.lower() in text) or (list_states[state].lower() in text):  # initial date validation and location check
                            manual_check_results.append(result)
                            manual_check_link_attributes[link] = 1
                    
                    elif text is not None and len(text) > 5000:
                        text = text.lower()
                        text = shorten_text(text)

                    else:
                        text = text.lower()


            except Exception as e:
                print(f"Error processing text from {link}: {e}")
                text = ""  # Set text to an empty string to ensure the remaining code can execute
                invalid_results.append(result)

            gpt_explanation = ""
            if result not in manual_check_results:
                if publish_date != "N/A" and int(publish_year) < 2014:  # initial date validation
                    invalid_results.append(result)
                elif (state.lower() not in text) and (list_states[state].lower() not in text):  # initial location check
                    invalid_results.append(result)
                else:
                    if link not in link_attributes:  # check if link has already been seen, not seen create new entry in dictionary
                        gpt_res, gpt_explanation = analyze_with_chatgpt(text, state, location, start, end)
                        link_attributes[link] = [location, end, gpt_explanation, gpt_res]  # add new link to the dictionary
                        # print(link_attributes)  # for testing
                        if gpt_res == True:
                            valid_results.append(result)
                        else:
                            invalid_results.append(result)     
                    else:
                        if location.lower() != link_attributes[link][0].lower() or date > link_attributes[link][1]:  # different location or later than current end date
                            gpt_res, gpt_explanation = analyze_with_chatgpt(text, state, location, start, end)
                            link_attributes[link] = [location, end, gpt_explanation, gpt_res]  # update dictionary contents
                            if gpt_res == True:
                                valid_results.append(result)   
                        else:
                            gpt_explanation = link_attributes[link][2]
                            if link_attributes[link][3] == True:
                                valid_results.append(result)
                    
            # write the contents of the valid results file
            with open(csv_path1, 'a', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=["County", "State", "Arrest_Date", "Start_Date_Param", "End_Date_Param", "Article_Title", "Article_Link", "Article_Date", "LLM_Analysis", "StateCountyFIPS", "FIPSState", "FIPSCounty"])
                for result in valid_results:
                    writer.writerow({
                        # "Query": query,
                        "County": county,
                        "State": state,
                        "Arrest_Date": date,
                        "Start_Date_Param": start,
                        "End_Date_Param": end,
                        "Article_Title": result.get("title", "N/A"),
                        "Article_Link": result.get("link", "N/A"),
                        "Article_Date": result.get("date", "N/A"),
                        "LLM_Analysis": gpt_explanation,
                        "StateCountyFIPS": scFIPs,
                        "FIPSState": fipsS,
                        "FIPSCounty": fipsC
                    })
            # write the contents of the invalid results file
            with open(csv_path2, 'a', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=["County", "State", "Title", "Link", "Date", "LLM_Analysis"])
                for result in invalid_results:
                    writer.writerow({
                        # "Query": query,
                        "Title": result.get("title", "N/A"),
                        "Link": result.get("link", "N/A"),
                        "Date": result.get("date", "N/A"),
                        "County": county,
                        "State": state,
                        "LLM_Analysis": gpt_explanation
                    })
            # write the contents of the manual check results file
            with open(csv_path3, 'a', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=["County", "State", "Title", "Link", "Date"])
                for result in manual_check_results:
                    writer.writerow({
                        # "Query": query,
                        "Title": result.get("title", "N/A"),
                        "Link": result.get("link", "N/A"),
                        "Date": result.get("date", "N/A"),
                        "County": county,
                        "State": state
                    })
    except Exception as e:
        print(f"Error during search or file writing: {e}")


# helper function to reduce text from scraped results
def shorten_text(text):
    new_text = text[:12288] # 88888 for gpt-4-turbo
    return new_text


# helper function to scrape text of links from google search api results
def scrape_article_text(url, timeout=8):  # puts a timer of 15 seconds total
    class ContentFetcher(threading.Thread):
        def __init__(self, url):
            super().__init__()
            self.url = url
            self.result = None

        def run(self):
            try:
                response = requests.get(self.url, timeout=6)  # ensures the request itself also respects a timeout
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    self.result = soup.get_text(separator=' ', strip=True)
            except Exception as e:
                print(f"Error scraping {self.url}: {e}")
                self.result = None

    fetcher = ContentFetcher(url)
    fetcher.start()
    fetcher.join(timeout)
    if fetcher.is_alive():
        fetcher.result = False  # indicate a timeout happened
        fetcher.join()  # ensure the thread finishes

    return fetcher.result  # returns scraped text if success, None if error with scraping, or False if over time limit


# helper function to check validity of the links
def analyze_with_chatgpt(text, state, location, start, end):
    openai.api_key = open_ai_key
    # print(date, location)

    # questions we ask gpt to check
    questions = [
        f"Here are four questions, please answer them all.\
        \n1. Does this text mention {location} or {state}? Explicitly say yes or no in the first word of your response along with your explanation.\
        \n2. Is the text related to immigration raids/arrests? Explicitly say yes or no in the first word of your response along with your explanation.\
        \n3. Does this text mention the date and is the date of this immigration raid between {start} and {end}? Explicitly say yes or no in the first word of your response along with your explanation.\
        \n4. Does this text confirm that the raid was conducted by Immigration and Customs Enforcement? Explicitly say yes or no in the first word of your response along with your explanation."


        # number of arrests that occured in this specific {county} if mention
        # total arrests of this immigration raid if mention
        # list the city in this immigration raid
        # list the county name
        # target 12345
        # how many of them have previous conviction
        # how many female if mention
        # what are the nationality of those people in this specific {county}
    ]
        
    # res = []
    for question in questions:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo", # change the gpt api version
            messages=[
                {"role": "system", "content": "Analyze the provided text for specific information."},
                {"role": "user", "content": text},
                {"role": "user", "content": question}
            ]
        )
        # print(question + ":")
        answer = response['choices'][0]['message']['content'].strip().lower() # grab the response from gpt for the question
        # words = answer.split()  # Split the response into words
        # print(answer + "\n")  # for debugging
        if "no." in answer or "no," in answer or "no " in answer or "no*" in answer:
            return False, answer
    
    # no longer need this since we only have one question
    # for item in res:
    #     if item == False:  # mark link as invalid if any of the gpt questions did not pass
    #         return False
    return True, answer


def search_and_export(data):
    # Get the path to the desktop directory
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    # Define the full path to the CSV files on the desktop
    output_csv1 = os.path.join(desktop_path, "valid_results.csv")
    output_csv2 = os.path.join(desktop_path, "invalid_results.csv")
    output_csv3 = os.path.join(desktop_path, "manual_check_results.csv")

    # check if the files exists, otherwise create and write the header
    if not os.path.exists(output_csv1):  # valid_results
        with open(output_csv1, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, ["County", "State", "Arrest_Date", "Start_Date_Param", "End_Date_Param", "Article_Title", "Article_Link", "Article_Date", "LLM_Analysis", "StateCountyFIPS", "FIPSState", "FIPSCounty"])
            writer.writeheader()
    if not os.path.exists(output_csv2):  # invalid_results
        with open(output_csv2, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=["County", "State", "Title", "Link", "Date"])
            writer.writeheader()
    if not os.path.exists(output_csv3):  # manual_check_results
        with open(output_csv2, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=["County", "State", "Title", "Link", "Date"])
            writer.writeheader()

    # Process each query and append results to the CSV
    # i = 0  # limit counter for testing
    for arrestdate, county, state, scFIPs, fipsS, fipsC in data:
        # if i == 20: break  # limit counter for testing
        # print(arrestdate)  # for debugging

        # since the dates provided in csv are m/dd/yy we need to fix the format year
        # year = arrestdate[-2:]
        # no_year = arrestdate[:-2]
        # year = "20" + year
        arrestdate = arrestdate[:-2] + "20" + arrestdate[-2:]
        # print(arrestdate)  # for testing

        start_date, end_date = calc_date(arrestdate)
        arrestdate = datetime.strptime(arrestdate, "%m/%d/%Y")
        arrestdate = arrestdate.strftime("%m/%d/%Y")
        # start_date, end_date = calc_date("2/10/2017")
        # print(start_date, end_date, county, state)  # for testing

        query = f"Immigration Raid/Arrest, {county}, {state}"  # the query sent to the helper function
        # query = f"Immigration Raid/Arrest, {"Terrebonne"}, {"LA"}"  # for testing
        # print(query)  # for testing
        helper(query, county, state, start_date, end_date, output_csv1, output_csv2, output_csv3, arrestdate, scFIPs, fipsS, fipsC)
        # helper(query, "Terrebonne", "LA", start_date, end_date, output_csv1, output_csv2, arrestdate)  # for testing
        
        # i += 1 # limit counter for testing

def main():
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    input_csv = os.path.join(desktop_path, "abnormal_arrest_dates.csv")
    data = parse_csv(input_csv)
    search_and_export(data)
    print(f"Organic search results have been exported to {os.path.join(desktop_path, 'valid_results.csv')}")
    print(f"Organic search results have been exported to {os.path.join(desktop_path, 'invalid_results.csv')}")
    print(f"Organic search results have been exported to {os.path.join(desktop_path, 'manual_check_results.csv')}")

main()