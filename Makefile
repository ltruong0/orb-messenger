# Starter Makefile. Claude Code: add the other targets listed in CLAUDE.md as each phase lands.
.PHONY: contract-test

contract-test:
	python3 contracts/validate.py
