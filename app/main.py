import logging

from app.settings import Settings

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    enabled = [name for name, book in settings.books.items() if book.enabled]
    log.info("arb-scanner starting, enabled books: %s", ", ".join(enabled) or "none")
    log.info("no scrapers yet, exiting")


if __name__ == "__main__":
    main()
