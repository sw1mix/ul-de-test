from etl.pipeline import run_etl
from ml.train_score import run_ml


def main() -> None:
    run_etl()
    run_ml()


if __name__ == "__main__":
    main()
