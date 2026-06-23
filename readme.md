Run `setup.sh` to set up.

To restart the job, run
`systemctl restart prizepicks-monitor`

To erase the cache of the tweets that have already been processed:
`rm /root/prizepicks-monitor/last_tweet_id.txt`

To watch logs:
`journalctl -u prizepicks-monitor -f`

---

Claude Code wrote this specifically for a ubuntu VPS. The script itself should be OS-agnostic, but the setup script and `systemd` usage may not work on a different OS.

