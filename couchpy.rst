#############################
couch -- A CouchDB client API
#############################

A CouchDB_ client API for Python 3000, ported from the Python (2.x) client code written by **Christopher Lenz**, CouchDB-Python_, but refactored to be used with Python 3.x interpreters (only).

.. automodule:: couchpy

As the `couchpy` package has been adapted to work with Python 3000, some parts of the original client API have changed, and only the very essential parts of Christopher's package (ie., only the `client` (now called `broker`) and `http` (now `network`)) have been ported. All tools provided in addition by CouchDB-Python_ are not available and not planned. The http/network module is substantially different from the original to be useful with Python 3000's modified :mod:`io` and :mod:`http.client` modules , while the client/broker API itself looks almost maintained "as is" from the outside except for some renamed methods, but everything that can be streamed from the CouchDB (``Transfer-Encoding: chunked``) is now by default handed on as such - eg., :meth:`.couch.broker.Database.list` views return data streams.

All relevant classes and exceptions can be directly imported from the `couch` module itself::

    from couchpy import *

That statement provides the following classes and exceptions in your namespace: :class:`.broker.Database`, :class:`.broker.Document`, and :class:`.broker.Server`, as well as the the exceptions:

 * :exc:`couchpy.network.HTTPError`,
 * :exc:`couchpy.network.PreconditionFailed`,
 * :exc:`couchpy.network.RedirectLimitExceeded`,
 * :exc:`couchpy.network.ResourceConflict`,
 * :exc:`couchpy.network.ResourceNotFound`,
 * :exc:`couchpy.network.ServerError`, and
 * :exc:`couchpy.network.Unauthorized`.

All these errors treat connection, request and response problems as well as errors reported by CouchDB itself, and are all based on :exc:`http.client.HTTPException`. Also, string encoding problems are reported as :exc:`UnicodeError`, JSON encoding problems as :exc:`TypeError`, and bad method parameters as :exc:`ValueError`. Virtually all methods provided through the `broker` classes might raise any of them, so if you need strict error recovery, wrap all calls to this API with `except` clauses for these four errors.

In addition to the three standard fields CouchDB sets on documents (**_id**, **_rev**, **_attachments**), the broker also adds the fields **created** and **modified** to every document upon saving it, as extended `ISO 8601`_ **UTC** (ie., without timezone) timestamps: "YYYY-MM-DDTHH:MM:SS". In the case that "created" is not set, both fields will be set to the same value. Otherwise, only the "modified" timestamp is replaced.

.. _CouchDB: http://couchdb.apache.org/
.. _CouchDB-Python: http://code.google.com/p/couchdb-python
.. _ISO 8601: http://en.wikipedia.org/wiki/ISO_8601

===================================
broker -- The Couch Database Broker
===================================

The broker module has been largely left untouched from the original code by
Christopher Lenz for CouchDB-Python (in ``client.py``) *at the API level*, but
much of the internals have completely changed. However, even if you are
familiar with the original API, it is recommendable to at least once check
the documentation of each public method for changes.

A simple usage example; Create a database and/or fetch the database handle:

>>> from couchpy import Server
>>> server = Server()
>>> db = server['python-tests']

Check if the database is available and if it exists in the Couch server:

>>> if db: print('online')
online
>>> 'python-tests' in server
True
>>> 'python-tests' in list(server) # a list of all DBs in the server
True

Add a document to the database, using the ID ``'example'``, and fetch it:

>>> doc = {'type': 'Person', 'name': 'John Doe'}
>>> db['example'] = doc
>>> doc['_id'] # Note that _id, _rev, created and modified are added/modified
'example'
>>> doc = db['example']
>>> doc['type']
'Person'
>>> doc['name']
'John Doe'
>>> doc.id
'example'
>>> doc.rev is not None
True

Documents always are supplied with creation and modification dates:

>>> doc.created is not None
True
>>> doc.modified is not None
True
>>> doc.created == doc.modified
True

List the document (IDs), count the number of documents, or check for the existence of a document (ID) in the database:

>>> list(db)
['example']
>>> len(db)
1
>>> doc.id in db
True

Delete a document from the database:

>>> del db[doc.id]
>>> doc.id in db
False

Delete a database:

>>> del server['python-tests']

.. automodule:: couchpy.broker

.. autodata:: couchpy.broker.COUCHDB_URL

Server
------

.. autoclass:: couchpy.broker.Server
    :members:

Database
--------

.. autoclass:: couchpy.broker.Database
    :members:

ViewResults
-----------

.. autoclass:: couchpy.broker.ViewResults
    :members:

Document
--------

.. autoclass:: couchpy.broker.Document
    :members:

Row
---

.. autoclass:: couchpy.broker.Row
    :members:

Attachment
----------

.. autoclass:: couchpy.broker.Attachment

===================================
network -- The Network Access Layer
===================================

.. automodule:: couchpy.network

.. autodata:: couchpy.network.USER_AGENT

Response
--------

.. autoclass:: couchpy.network.Response

ResponseStream
--------------

.. autoclass:: couchpy.network.ResponseStream
    :members:

Session
-------

.. autoclass:: couchpy.network.Session
    :members:

Resource
--------

.. autoclass:: couchpy.network.Resource
    :members:

HTTPError
---------

These errors might be raised when executing functions and methods of the :mod:`.broker` module.

.. autoclass:: couchpy.network.HTTPError

    HTTPError is based on :exc:`http.client.HTTPException`\ .

.. autoclass:: couchpy.network.PreconditionFailed

.. autoclass:: couchpy.network.RedirectLimitExceeded

.. autoclass:: couchpy.network.ResourceNotFound

.. autoclass:: couchpy.network.ResourceConflict

.. autoclass:: couchpy.network.Unauthorized

ServerError
-----------

This error is raised when something went wrong on the server (CouchDB) side, ie., HTTP 500 responses.

.. autoclass:: couchpy.network.ServerError

    ServerError is based on :exc:`http.client.HTTPException`\ .

==============================
serializer -- Data Serializers
==============================

b64encode
---------

.. autofunction:: couchpy.serializer.b64encode

b64decode
---------

.. autofunction:: couchpy.serializer.b64decode

