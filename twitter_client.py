import time
import tweepy
import logging

logger = logging.getLogger(__name__)

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_TYPES = {".mp4", ".mov", ".m4v"}


def _get_clients(api_key, api_secret, access_token, access_token_secret):
    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_token_secret)
    api_v1 = tweepy.API(auth)
    return client, api_v1


def _upload_video(api_v1: tweepy.API, path: str) -> str:
    """Upload a video using chunked upload and wait for processing."""
    media = api_v1.media_upload(
        filename=path,
        media_category="tweet_video",
        chunked=True,
    )
    media_id = str(media.media_id)
    logger.info(f"Video upload initiated: {path} -> {media_id}")

    # Wait for async processing to complete
    for _ in range(30):
        status = api_v1.get_media_upload_status(media_id)
        state = status.processing_info.get("state") if hasattr(status, "processing_info") and status.processing_info else "succeeded"
        logger.info(f"Video processing state: {state}")
        if state == "succeeded":
            break
        if state == "failed":
            raise RuntimeError(f"Video processing failed: {status.processing_info}")
        time.sleep(5)

    return media_id


def post_tweet(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_token_secret: str,
    text: str,
    image_paths: list[str] = [],
) -> str:
    client, api_v1 = _get_clients(api_key, api_secret, access_token, access_token_secret)

    media_ids = []
    for path in image_paths[:4]:
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        try:
            if ext in VIDEO_TYPES:
                media_id = _upload_video(api_v1, path)
                media_ids.append(media_id)
                # Twitter allows only 1 video per tweet
                break
            else:
                media = api_v1.media_upload(filename=path)
                media_ids.append(str(media.media_id))
                logger.info(f"Image uploaded: {path} -> {media.media_id}")
        except Exception as e:
            logger.warning(f"Failed to upload media {path}: {e}")

    kwargs = {"text": text}
    if media_ids:
        kwargs["media_ids"] = media_ids

    response = client.create_tweet(**kwargs)
    return str(response.data["id"])


def verify_credentials(api_key, api_secret, access_token, access_token_secret) -> dict:
    client, _ = _get_clients(api_key, api_secret, access_token, access_token_secret)
    me = client.get_me()
    return {"id": me.data.id, "username": me.data.username, "name": me.data.name}
