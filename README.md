# GitHub PR Summarizer

A FastAPI application that automatically summarizes pull requests by commenting on them when they are opened, synchronized, or reopened.

## Setup

### 1. Create a GitHub App

1. Go to [GitHub Settings > Developer settings > GitHub Apps](https://github.com/settings/apps)
2. Click "New GitHub App"
3. Fill in the basic information:
   - **App name**: `pr-summarizer` (or any name you prefer)
   - **Homepage URL**: `http://localhost:8000` (for development)
   - **Webhook URL**: `http://localhost:8000/webhooks/github` (for development)
   - **Webhook secret**: Generate a random secret (you'll need this later)

4. Set permissions:
   - **Repository permissions**:
     - `Pull requests`: Read & Write
     - `Contents`: Read

5. Create the app and note down:
   - **App ID** (you'll see this on the app page)
   - **Webhook secret** (the one you set earlier)

6. Generate a private key:
   - Go to "Private keys" section
   - Click "Generate private key"
   - Download the `.pem` file

### 2. Environment Setup

1. Copy the environment template:
   ```bash
   cp env_template.txt .env
   ```

2. Edit the `.env` file with your actual values:
   ```bash
   # Replace with your actual GitHub App ID
   GITHUB_APP_ID=123456
   
   # Replace with your private key content (the entire PEM file content)
   GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
   your_actual_private_key_content_here
   -----END RSA PRIVATE KEY-----"
   
   # Replace with your webhook secret
   GITHUB_WEBHOOK_SECRET=your_webhook_secret_here
   ```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Application

```bash
uvicorn main:app --reload --port 8000
```

The application will now start and provide helpful error messages if environment variables are missing.

### 5. Install the App on Your Repository

1. Go to your GitHub App's page
2. Click "Install App"
3. Choose the repository where you want to use the PR summarizer
4. The app will now receive webhook events from that repository

## How It Works

1. When a pull request is opened, synchronized, or reopened, GitHub sends a webhook to your application
2. The application verifies the webhook signature for security
3. It fetches the list of files changed in the PR
4. It posts a comment on the PR with a summary of the changes

## Development

- The application runs on `http://localhost:8000`
- Webhook endpoint: `http://localhost:8000/webhooks/github`
- Health check: `http://localhost:8000/healthz`

## Notes

- For production, you'll need to deploy this to a publicly accessible URL
- Update the webhook URL in your GitHub App settings to point to your production URL
- Consider using environment-specific configuration for different deployment environments
