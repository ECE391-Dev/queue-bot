tmux new-session -d "cd ~/queue_bot; pkill -f python3; git pull; python3 -m venv venv; source venv/bin/activate; pip3 install -r requirements.txt; python3 queue_bot.py | tee output.log"
