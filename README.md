# TeraBox Downloader API

A simple Flask API to bypass TeraBox ads and proxy downloads. 

## Deployment to Render

1. Create a new Web Service on [Render](https://render.com).
2. Connect your GitHub repository.
3. Use the following settings:
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app -b 0.0.0.0:$PORT`
4. In Environment Variables, add:
   - `NDUS_COOKIE`: Your TeraBox NDUS cookie.
5. Click **Deploy**.

## Endpoints
- `/api/terabox?url=YOUR_URL`
