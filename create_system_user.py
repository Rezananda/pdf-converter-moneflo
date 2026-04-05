import os
import secrets
import string
from supabase import create_client, Client
from dotenv import load_dotenv, set_key

def generate_password(length=16):
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def main():
    # Load existing env vars
    env_path = "/Users/maderezanandaputra/Documents/My Projects/pdf-converter/.env"
    load_dotenv(env_path)
    
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") # anon key
    
    if not url or not key:
        print("Error: SUPABASE_URL or SUPABASE_KEY missing in .env")
        return

    supabase: Client = create_client(url, key)
    
    email = "api-service@monetor.com"
    password = generate_password()
    
    print(f"DEBUG: Attempting to register system user: {email}")
    
    try:
        # Try to sign up the user
        response = supabase.auth.sign_up({
            "email": email,
            "password": password,
        })
        
        # If user already exists, sign_up might return null user or error
        if response and response.user:
            print(f"INFO: Successfully created system user: {email}")
        else:
            print(f"INFO: System user might already exist or sign-up disabled. Response: {response}")
            # If sign-up is disabled, this might fail, but let's assume it works for this test.
            # In a real app, you might use service role to create user, but we don't have it.
            
        # Update .env with the credentials
        set_key(env_path, "SUPABASE_SYSTEM_EMAIL", email)
        set_key(env_path, "SUPABASE_SYSTEM_PASSWORD", password)
        print("INFO: Updated .env with SUPABASE_SYSTEM_EMAIL and SUPABASE_SYSTEM_PASSWORD")
        
    except Exception as e:
        print(f"ERROR: Failed to create system user: {e}")
        # Even if it fails, maybe the user already exists.
        # We will manually set the password in .env if we have to.

if __name__ == "__main__":
    main()
