Here is the Google Search Key we use: https://serpapi.com/search-api

1. Go to https://serpapi.com/ to sign in, then go to https://serpapi.com/manage-api-key to generate Google Search API key and add billing informatiion

2. Go to https://openai.com/index/openai-api to sign up, then go to https://platform.openai.com/api-keys to create OpenAI API key and add billing information

3. Replace with your own API keys in api_keys.env

4. Activate virtual environment: source myenv/bin/activate

5. Install packages: pip install -r requirements.txt

If it doesn't work, then try line by line: 
pip install requests
pip install beautifulsoup4
pip install openai
pip install google-search-results
pip install serpapi

6. python main.py

For GNews Module:
pip install tqdm
pip install gnews

The code is ready to be run after this. 
