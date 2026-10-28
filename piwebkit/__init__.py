"""What the small LAN web pages in this repo (simpleaprs, simplemesh) have in common, and nothing of either.

hub        fans events out to connected pages (Server-Sent Events): the last month of packets and all messages, then live
server     a threaded HTTP server: Host/Origin checks against other web sites, JSON bodies, event streams
auth       one login, scrypt passwords, sessions kept across restarts
webbase    the page's login, logout and password endpoints, and a base class for an app's own routes
maps       the offline map: a proxy for a Project N.O.M.A.D. map server, so the browser sees one origin
tilecache  the disk cache behind that proxy
web/       the login page and the MapLibre and PMTiles files the map pages load

Apps depend on this package. It depends on no app, and apps don't depend on each other (tests/test_independence.py).
"""
