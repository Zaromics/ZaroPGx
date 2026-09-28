# data/versions

Each service writes `<service>.json` here at startup, `{"name": ..., "version": ...}`.
The report's methods and citations read them. The JSON files are runtime output and
are not tracked.

A manifest may also name the versions of the packages inside the service, as ZaroHLA
does for OptiType.
