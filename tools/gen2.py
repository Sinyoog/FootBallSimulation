import sys, random, os, time
sys.path.insert(0, sys.argv[1]); os.chdir(sys.argv[1])
seed=int(sys.argv[2]); random.seed(seed)
import database
database.DB_PATH=os.path.join(sys.argv[1], f"r{seed}.db")
database.init_db()
database.seed_initial_data(skip_ai_regen=True)
random.seed(seed)
database.reset_game_data()
try: database.flush_to_disk()
except Exception as e: print("flush",e)
print("done")