import time
import requests
import jwt
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

def generate_jwt(api_key, api_secret, method, path):
    """
    Generate a JWT for Coinbase API authentication.
    """
    now = int(time.time())
    payload = {
        "sub": api_key,
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,  # Expires in 2 minutes
        "uri": f"{method} {path}"
    }
    token = jwt.encode(payload, api_secret, algorithm="HS256")
    return token

def get_accounts(limit=None, cursor=None, retail_portfolio_id=None):
    """
    Fetch list of brokerage accounts.
    Optional params: limit (int), cursor (str), retail_portfolio_id (str).
    """
    base_url = "https://api.coinbase.com"
    path = "/api/v3/brokerage/accounts"
    method = "GET"
    
    # Get API credentials from environment
    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_SIGNING_KEY")
    
    if not api_key or not api_secret:
        raise ValueError("COINBASE_API_KEY or COINBASE_SIGNING_KEY not found in .env file")

    # Build query string if params are provided
    query_params = []
    if limit:
        query_params.append(f"limit={limit}")
    if cursor:
        query_params.append(f"cursor={cursor}")
    if retail_portfolio_id:
        query_params.append(f"retail_portfolio_id={retail_portfolio_id}")
    query_string = "?" + "&".join(query_params) if query_params else ""
    
    jwt_path = path + query_string
    
    jwt_token = generate_jwt(api_key, api_secret, method, jwt_path)
    print("System Time (Unix):", int(time.time()))  # Debug: Current system time
    print("Generated JWT:", jwt_token)  # Debug: Print the token
    
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json"
    }
    
    url = base_url + path + query_string
    print("Request URL:", url)  # Debug: Print the request URL
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        print("Response Text:", response.text)  # Debug: Print error details
        raise Exception(f"Error: {response.status_code} - {response.text}")

# Example usage
if __name__ == "__main__":
    try:
        accounts_data = get_accounts()  # Add params like limit=10 if needed
        print("Accounts Data:", accounts_data)
    except Exception as e:
        print("Exception:", e)