# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for a security problem. Report it privately instead:
**[Report a vulnerability](https://github.com/edstagdh/PMV_Nexus/security/advisories/new)** (GitHub's private
vulnerability reporting — only you and the maintainer see it).

Include what you found, how to reproduce it, the version (Help → About) and how the app is run (Windows Setup,
service, `install.sh`, behind a reverse proxy...). Leave out passwords, API keys and anything else private.

You'll get an answer within a week. A fix goes into a new release, and the report is credited in the advisory if
you'd like.

## Supported versions

Only the latest release gets security fixes — update with the newest Setup (or `install.sh` / `install.bat`).

## Scope

PMV Nexus is meant to run on your own computer or home server, for you and the people you live with. Worth
reporting: anything that lets someone without an account (or with fewer permissions) see or change things, reach
the files or credentials, or run programs on the computer. Not in scope: problems that need an admin account or
access to the computer itself, and the sites the app works with.
