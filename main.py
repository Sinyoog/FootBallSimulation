"""
main.py - 진입점
"""
import sys
from PyQt6.QtWidgets import QApplication
from database import init_db, seed_initial_data, sync_countries
from ui.start_screen import StartScreen


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # DB 초기화 (최초 1회 데이터 삽입)
    init_db()
    seed_initial_data()
    sync_countries()   # COUNTRY_DATA 변경분 기존 세이브에 반영

    win = StartScreen()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()