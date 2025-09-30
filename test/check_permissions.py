import requests
import json
import time
import jwt
from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

API_KEY_NAME = os.getenv('COINBASE_API_KEY')
PRIVATE_KEY = os.getenv('COINBASE_SIGNING_KEY')
API_URL = "https://api.coinbase.com/api/v3/brokerage/key_permissions"

def generate_jwt_token(api_key_name, private_key_string, request_method, request_path):
    """
    Generate a JWT token for Coinbase CDP API authentication.
    """
    try:
        # Replace escaped newlines with actual newlines
        private_key_string = private_key_string.replace('\\n', '\n')
        
        # Load the private key from string
        private_key = serialization.load_pem_private_key(
            private_key_string.encode(),
            password=None
        )
        
        # Create JWT URI (method + host + path)
        uri = f"{request_method} api.coinbase.com{request_path}"
        
        # Create JWT payload
        current_time = int(time.time())
        payload = {
            'sub': api_key_name,
            'iss': 'coinbase-cloud',
            'nbf': current_time,
            'exp': current_time + 120,  # Token expires in 2 minutes
            'uri': uri
        }
        
        # Generate JWT token
        token = jwt.encode(
            payload,
            private_key,
            algorithm='ES256',
            headers={'kid': api_key_name, 'nonce': str(current_time)}
        )
        
        return token
    
    except Exception as e:
        print(f"Error generating JWT token: {e}")
        return None

def check_api_key_permissions():
    """
    Check the permissions of your Coinbase CDP API key.
    """
    # Verify environment variables are loaded
    if not API_KEY_NAME or not PRIVATE_KEY:
        print("✗ Error: Missing environment variables!")
        print("\nPlease ensure your .env file contains:")
        print("  COINBASE_API_KEY=organizations/your-org-id/apiKeys/your-key-id")
        print("  COINBASE_SIGNING_KEY=-----BEGIN EC PRIVATE KEY-----\\n...\\n-----END EC PRIVATE KEY-----")
        return
    
    request_method = "GET"
    request_path = "/api/v3/brokerage/key_permissions"
    
    print("Generating JWT token...")
    token = generate_jwt_token(API_KEY_NAME, PRIVATE_KEY, request_method, request_path)
    
    if not token:
        return
    
    print("Token generated successfully!")
    print("\nMaking API request...")
    
    # Set up headers
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    
    try:
        # Make the API request
        response = requests.get(API_URL, headers=headers)
        
        print(f"\nResponse Status Code: {response.status_code}")
        
        if response.status_code == 200:
            # Parse and display the response
            data = response.json()
            print("\n" + "="*50)
            print("API KEY PERMISSIONS")
            print("="*50)
            print(f"Can View:        {data.get('can_view', 'N/A')}")
            print(f"Can Trade:       {data.get('can_trade', 'N/A')}")
            print(f"Can Transfer:    {data.get('can_transfer', 'N/A')}")
            print(f"Portfolio UUID:  {data.get('portfolio_uuid', 'N/A')}")
            print(f"Portfolio Type:  {data.get('portfolio_type', 'N/A')}")
            print("="*50)
            print("\n✓ API key is working correctly!")
            
        else:
            print(f"\n✗ Error occurred!")
            print(f"Response: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Request failed: {e}")
    except json.JSONDecodeError as e:
        print(f"\n✗ Failed to parse response: {e}")

if __name__ == "__main__":
    print("Coinbase CDP API Key Permissions Checker")
    print("="*50)
    check_api_key_permissions()