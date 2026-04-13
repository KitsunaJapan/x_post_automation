import tweepy
import logging

logger = logging.getLogger(__name__)


def _get_client(api_key: str, api_secret: str, access_token: str, access_token_secret: str):
    """Return an authenticated Tweepy Client (API v2) + API v1.1 for media upload."""
    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )
    # v1.1 auth needed for media upload
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_token_secret)
    api_v1 = tweepy.API(auth)
    return client, api_v1


def post_tweet(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_token_secret: str,
    text: str,
    image_paths: list[str] = [],
) -> str:
    """
    Post a tweet with optional images.
    Returns the tweet ID as a string.
    """
    client, api_v1 = _get_client(api_key, api_secret, access_token, access_token_secret)

    media_ids = []
    for path in image_paths[:4]:  # Twitter allows up to 4 images
        try:
            media = api_v1.media_upload(filename=path)
            media_ids.append(str(media.media_id))
            logger.info(f"Uploaded media: {path} -> {media.media_id}")
        except Exception as e:
            logger.warning(f"Failed to upload image {path}: {e}")

    kwargs = {"text": text}
    if media_ids:
        kwargs["media_ids"] = media_ids

    response = client.create_tweet(**kwargs)
    tweet_id = str(response.data["id"])
    return tweet_id


def verify_credentials(api_key: str, api_secret: str, access_token: str, access_token_secret: str) -> dict:
    """Check that credentials are valid. Returns user info dict."""
    client, _ = _get_client(api_key, api_secret, access_token, access_token_secret)
    me = client.get_me()
    return {"id": me.data.id, "username": me.data.username, "name": me.data.name}
