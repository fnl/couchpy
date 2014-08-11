###############################
couchpy -- A CouchDB client API
###############################

A CouchDB_ client API for Python 3000, ported from the Python (2.x) client code written by **Christopher Lenz**, CouchDB-Python_, but refactored to be used with Python 3.x interpreters (only).

As the ``couchpy`` package has been adapted to work with Python 3000, some parts of the original client API have changed, and only the very essential parts of Christopher's package (ie., only the ``client`` (now called ``broker``) and ``http`` (now ``network``)) have been ported.
All tools provided in addition by CouchDB-Python_ are not available and not planned.
The http/network module is substantially different from the original to be useful with Python 3000's modified `io` and `http.client` modules , while the client/broker API itself looks almost maintained "as is" from the outside except for some renamed methods, but everything that can be streamed from the CouchDB (``Transfer-Encoding: chunked``) is now by default handed on as such - eg., `couchpy.broker.Database.list` views return data streams.

All relevant classes and exceptions can be directly imported from the `couchpy` module itself::

    from couchpy import *

That statement provides the following classes and exceptions in your namespace: `couchpy.broker.Database`, `couchpy.broker.Document`, and `couchpy.broker.Server`, as well as the the exceptions:

* `couchpy.network.HTTPError`
* `couchpy.network.PreconditionFailed`
* `couchpy.network.RedirectLimitExceeded`
* `couchpy.network.ResourceConflict`
* `couchpy.network.ResourceNotFound`
* `couchpy.network.ServerError`
* `couchpy.network.Unauthorized`

All these errors treat connection, request and response problems as well as errors reported by CouchDB itself, and are all based on `http.client.HTTPException`.
Also, string encoding problems are reported as `UnicodeError`, JSON encoding problems as `TypeError`, and bad method parameters as `ValueError`.
Virtually all methods provided through the ``broker`` classes might raise any of them, so if you need strict error recovery, wrap all calls to this API with ``except`` clauses for these four errors.

In addition to the three standard fields CouchDB sets on documents (**_id**, **_rev**, **_attachments**), the broker also adds the fields **created** and **modified** to every document upon saving it, as extended `ISO 8601`_ **UTC** (ie., without timezone) timestamps: "YYYY-MM-DDTHH:MM:SS".
In the case that "created" is not set, both fields will be set to the same value.
Otherwise, only the "modified" timestamp is replaced.

.. _CouchDB: http://couchdb.apache.org/
.. _CouchDB-Python: http://code.google.com/p/couchdb-python
.. _ISO 8601: http://en.wikipedia.org/wiki/ISO_8601
