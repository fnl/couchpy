"""
.. py:module:: broker
   :synopsis: A CouchDB client over HTTP.

.. moduleauthor:: Christopher Lenz
.. moduleauthor:: Florian Leitner <florian.leitner@gmail.com>
.. License: GNU Affero GPL v3 (http://www.gnu.org/licenses/agpl.html)
"""
from collections import namedtuple
from datetime import datetime
from inspect import getsource
from io import TextIOBase
import mimetypes
import os
import re
from textwrap import dedent
from types import FunctionType
from urllib.parse import quote, urlencode

from couchpy import network, serializer

__all__ = ['Server', 'Database', 'Document', 'ViewResults', 'Row']
__docformat__ = 'restructuredtext en'


COUCHDB_URL = os.environ.get('COUCHDB_URL', 'http://localhost:5984/')
"""
The default CouchDB URL, either ``http://localhost:5984/`` or fetched from the
environment.
"""

VALID_DB_NAME = re.compile(r'^[a-z][a-z0-9_$()+-/]*$')
"""
A RegEx describing valid database names.
"""

SPECIAL_DB_NAMES = frozenset(('_users',))
"""
Built-in DB names not matching the :data:`.VALID_DB_NAME` RegEx.
"""


Attachment = namedtuple("Attachment", "content_type encoding data stream")
"""
The returned named tuple for :meth:`.Database.getAttachment`.

.. attribute:: content_type

    The ``Content-Type`` header string.

.. attribute:: encoding

    The ``charset`` value of `data` or ``None``.

.. attribute:: data

    The attachment itself, as a `bytes`, or ``None`` if streamed.

.. attribute:: stream

    A :class:`.network.ResponseStream` object if CouchDB sends the attachment
    as a chunked response or ``None`` if not streamed.
"""


def CallViewlike(resource:network.Resource, doc_ids:list,
                 options:dict) -> network.Response:
    """Call a resource that takes view-like options."""
    if options:
        options = EncodeViewOptions(options)

    if doc_ids:
        keys = {'keys': list(doc_ids)}
        return resource.postJson(json=keys, chunked=True, **options)
    else:
        assert "keys" not in options, "for keys, use the doc_ids method param"
        return resource.getJson(chunked=True, **options)


def DesignPathFromName(name:str, type:str) -> [str]:
    """
    Expand a 'design-doc/foo' style name to its full path as a list of
    segments.

    If *name* starts with '_', just split the name at all slashes. Otherwise,
    split the name on the first slash and return the following segments:
    ``['_design', name_1, type, name_2]``, which is useful to handle
    the special design document paths from the Views API.
    """
    if name.startswith('_'):
        return name.split('/')

    design, name = name.split('/', 1)
    return ['_design', design, type, name]


def DocPath(id:str) -> list:
    """
    Return the path segments for the given document *ID*.

    Splits IDs that start with a reserved segment (starting with '_'), e.g.
    ``"_design/foo/bar"`` at the first ``/``, resulting in two segments:
    ``["_design", "foo/bar"].
    """
    if id[:1] == '_':
        return id.split('/', 1)
    else:
        return [id]


def DocResource(base:network.Resource, id:str) -> network.Resource:
    """
    Return a new resource for the given document *ID* starting from a *base*
    :class:`.network.Resource` using :func:`.DocPath`.
    """
    return base(*DocPath(id))


def EncodeViewOptions(options:dict) -> dict:
    """
    Encode any values in *options* as JSON unless they are strings.
    """
    retval = dict()

    for name, value in list(options.items()):
        if name in ('key', 'startkey', 'endkey') \
                or not isinstance(value, str):
            value = serializer.Encode(value)

        retval[name] = value

    return retval


def SetTimestamps(document:dict, now:datetime=None) -> dict:
    """
    Add a field **created** to *document* if it does not exist and set
    the field **modified** on *document* no matter if it exists or not.

    Instead of fetching the UTC time at the moment the function is called, the
    *now* value of the timestamp can be given; The given datetime will be used
    after isoformatting the timestamps instead of :meth:`datetime.now`.

    :param document: A document to update with created and modified fields.
    :param now: The `datetime` value to set (default: :meth:`datetime.now`).
    :return: A dictionary containing the old created and modified values (if
        any).
    """
    old_stamps = {stamp: document[stamp]
                  for stamp in ('created', 'modified') if stamp in document}

    if now is None:
        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    else:
        now = now.strftime('%Y-%m-%dT%H:%M:%S')

    if 'created' not in document:
        document['created'] = now

    document['modified'] = now
    return old_stamps


def UnsetTimestamps(document:dict, old_stamps:dict):
    """
    Remove **created** and **modified** values from *document* if they do not
    exist in *old_stamps* or update them to the value in *old_stamps* if they
    do.

    :param document: The document to update.
    :param old_stamps: A dictionary, possibly with created and modified keys.
    """
    for stamp in ('created', 'modified'):
        if stamp in old_stamps:
            document[stamp] = old_stamps[stamp]
        else:
            del document[stamp]


def ValidateDbName(name:str) -> str:
    """
    Return the name if it is a valid DB name and raise a :exc:`ValueError`
    otherwise.
    """
    if not name or (name not in SPECIAL_DB_NAMES and not VALID_DB_NAME.match(name)):
        raise ValueError('invalid database name {}'.format(repr(name)))

    return name


class Document(dict):
    """
    Representation of a document in the database.

    This is basically just a dictionary with the two additional properties
    `id` and `rev`, which contain the document ID and revision, respectively.
    """

    def __repr__(self):
        return '<{} {}@{}>'.format(type(self).__name__, self.id, self.rev)

    def _toTimestamp(self, name:str) -> datetime:
        ts = self.get(name)

        if ts:
            ts = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S')

        return ts

    @property
    def id(self) -> str:
        """
        The document ID.
        """
        return self['_id']

    @id.setter
    def id(self, _id:str):
        """
        Set the document ID.
        """
        self['_id'] = _id

    @property
    def rev(self) -> str:
        """
        The document revision or ``None``.
        """
        return self.get('_rev')

    @rev.setter
    def rev(self, _rev:str):
        """
        Set the document revision.
        """
        self['_rev'] = _rev

    @property
    def created(self) -> datetime:
        """
        The documents **created** timestamp as `datetime` or ``None``.
        """
        return self._toTimestamp('created')

    @created.setter
    def created(self, ts:datetime):
        """
        Set the documents creation timestamp.
        """
        self['created'] = ts.isoformat()

    @property
    def modified(self) -> datetime:
        """
        The documents **modified** timestamp as `datetime` or ``None``.
        """
        return self._toTimestamp('modified')

    @modified.setter
    def modified(self, ts:datetime):
        """
        Set the documents creation timestamp.
        """
        self['modified'] = ts.isoformat()

    @property
    def attachments(self) -> dict:
        """
        Any attachments the document has, as a dictionary of file names
        pointing to dictionaries describing the file; or an empty dictionary.
        """
        return self.get('_attachments', {})

    @attachments.setter
    def attachments(self, atts:dict):
        """
        Set the attachments dictionary if not ``None`` or empty.
        """
        if atts:
            self['_attachments'] = atts


class Row(dict):
    """
    Representation of a row returned by database views.
    """

    def __repr__(self) -> str:
        keys = 'id', 'key', 'error', 'value'
        items = ['%s=%r' % (k, self[k]) for k in keys if k in self]
        return '<%s %s>' % (type(self).__name__, ', '.join(items))

    @property
    def id(self) -> str:
        """
        The associated Document ID if it exists or ``None`` when it
        doesn't (reduce results).
        """
        return self.get('id')

    @property
    def key(self) -> str:
        """
        The ``key`` of this row.
        """
        return self['key']

    @property
    def value(self) -> object:
        """
        The ``value`` of this row, or ``None`` if it doesn't exist.
        """
        return self.get('value')

    @property
    def error(self) -> object:
        """
        The ``error`` message, or ``None`` if it doesn't exist.
        """
        return self.get('error')

    @property
    def doc(self) -> Document:
        """
        The associated document for the row. This is only present when the
        view was accessed with ``include_docs=True`` as a query parameter,
        otherwise this property will be ``None``.
        """
        doc = self.get('doc')
        if doc:
            return Document(doc)


class ViewResults:
    """
    A Manager for parametrized view (either permanent or temporary) results.

    This class allows re-evaluating the view using either the ``key`` option
    in index notation on it, or the ``startkey`` and ``endkey`` options using
    Python slice notation.

    >>> server = Server()
    >>> db = server.create('python-tests')
    >>> db['johndoe'] = dict(type='Person', name='John Doe')
    >>> db['maryjane'] = dict(type='Person', name='Mary Jane')
    >>> db['gotham'] = dict(type='City', name='Gotham City')
    >>> map_fun = '''function(doc) {
    ...     emit([doc.type, doc.name], doc.name);
    ... }'''
    >>> results = db.query(map_fun)

    At this point, the view has not actually been accessed yet. It is accessed
    as soon as it is iterated over, its length is requested, or one of its
    `rows`, `total_rows`, or `offset` properties are accessed:

    >>> len(results)
    3

    You can use slices to apply ``startkey`` and/or ``endkey`` options to the
    view:

    >>> people = results[['Person']:['Person','ZZZZ']]
    >>> for person in people:
    ...     print(person.value)
    John Doe
    Mary Jane
    >>> people.total_rows, people.offset
    (3, 1)

    Use plain indexed notation (without a slice) to apply the ``key`` option.
    Note that as CouchDB makes no claim that keys are unique in a view, this
    can still return multiple rows:

    >>> list(results[['City', 'Gotham City']])
    [<Row id='gotham', key=['City', 'Gotham City'], value='Gotham City'>]

    >>> del server['python-tests']
    """

    TOTAL_RE = re.compile(r'"total_rows"\s*:\s*(\d+)')
    OFFSET_RE = re.compile(r'"offset"\s*:\s*(\d+)')

    def __init__(self, view, doc_ids:list, options:dict):
        self.view = view
        self.doc_ids = list(doc_ids) if doc_ids else None
        self.options = options
        self._rows = self._total_rows = self._offset = None
        self.__refetch = False

    def __repr__(self) -> str:
        return '<%s %r %r>' % (type(self).__name__, self.view, self.options)

    def __getitem__(self, key):
        options = self.options.copy()

        if type(key) is slice:
            if key.start is not None:
                options['startkey'] = key.start
            if key.stop is not None:
                options['endkey'] = key.stop

            return ViewResults(self.view, self.doc_ids, options)
        else:
            options['key'] = key
            return ViewResults(self.view, self.doc_ids, options)

    def __iter__(self) -> iter:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(list(self.rows))

    def __fetch(self):
        data = self.view._exec(self.doc_ids, self.options)
        wrapper = self.view.wrapper

        if isinstance(data, network.ResponseStream):
            if not self.__refetch:
                self._rows = []
            stream = self.__genstream(data, self.__refetch)
            self.__refetch = True
            first_line = next(stream)
            self._total_rows = self._getTotal(first_line)
            self._offset = self._getOffset(first_line)
            rval = stream
        else:
            self._rows = [wrapper(row) for row in data['rows']]
            self._total_rows = data.get('total_rows', -1)
            self._offset = data.get('offset', 0)
            rval = self._rows

        return rval

    def __genstream(self, response:network.ResponseStream, noappend:bool):
        charset = response.charset
        wrapper = self.view.wrapper
        stream = iter(response)
        yield next(stream).decode(charset)  # pop the first line

        for line in stream:
            if len(line) > 5:  # skip lines that contain  row separators
                # remove trailing comma and make the line a list in case
                # more than one JSON object is present on the line
                line = line.decode(charset)
                if line[-1] == ',':
                    line = line[:-1]
                line = '[{}]'.format(line)

                try:
                    json = serializer.Decode(line)
                except ValueError:
                    msg = 'not valid JSON: "{}"'.format(line)
                    raise ValueError(msg)

                for obj in json:
                    row = wrapper(obj)
                    if not noappend:
                        self._rows.append(row)
                    yield row

        response.close()

    @staticmethod
    def _getIntValue(regex, line:str) -> int:
        try:
            return int(regex.search(line).group(1))
        except (ValueError, AttributeError):
            return None

    @classmethod
    def _getTotal(cls, line:str) -> int:
        total = cls._getIntValue(cls.TOTAL_RE, line)
        return total if total is not None else -1

    @classmethod
    def _getOffset(cls, line:str) -> int:
        return cls._getIntValue(cls.OFFSET_RE, line) or 0

    @property
    def rows(self):
        """
        An iterator over the rows or a list of them.

        Usually, each time this property is, they are loaded from the
        database, so it is better to store them locally, if you repeatedly
        need to work with the same rows. If the returned value is a `list`,
        the rows are stored locally; if it is an `iter`, they are fetched
        via HTTP streaming.
        """
        if not self._rows:
            if self._rows is None:
                return self.__fetch()
            elif self.__refetch:
                return self.__fetch()
            else:
                return self._rows
        else:
            return self._rows

    @property
    def total_rows(self) -> int:
        """
        The total number of rows in this view.

        This value is ``-1`` for reduce views or views with unknown sizes.
        """
        if self._total_rows is None: self.__fetch()
        return self._total_rows

    @property
    def offset(self) -> int:
        """
        The offset of the results from the first row in the view.

        This value is ``0`` for reduce views.
        """
        if self._offset is None: self.__fetch()
        return self._offset


class View:
    """
    Abstract representation of a view or query.
    """

    def __init__(self, url, wrapper=Row, session=None):
        if isinstance(url, str):
            self.resource = network.Resource(url, session)
        else:
            self.resource = url

        self.wrapper = wrapper

    def __call__(self, doc_ids:list=None, **options:dict) -> ViewResults:
        return ViewResults(self, doc_ids, options)

    def __iter__(self) -> iter([Row]):
        return iter(self())

    def _exec(self, doc_ids:list, options:dict) -> dict:
        raise NotImplementedError("abstract")


class PermanentView(View):
    """
    Representation of a permanent view on the server.
    """

    def __init__(self, url, name, wrapper=Row, session=None):
        View.__init__(self, url, wrapper=wrapper, session=session)
        self.name = name

    def __repr__(self) -> str:
        return '<%s %r>' % (type(self).__name__, self.name)

    def _exec(self, doc_ids:list, options:dict) -> dict:
        response = CallViewlike(self.resource, doc_ids, options)
        return response.data


class TemporaryView(View):
    """
    Representation of a temporary view.
    """

    def __init__(self, url, map_fun, reduce_fun=None,
                 language:str='javascript', wrapper=Row,
                 session:network.Session=None):
        View.__init__(self, url, wrapper=wrapper, session=session)

        if isinstance(map_fun, FunctionType):
            map_fun = getsource(map_fun).rstrip('\n\r')

        if reduce_fun:
            if isinstance(reduce_fun, FunctionType):
                reduce_fun = getsource(reduce_fun).rstrip('\n\r')

            reduce_fun = dedent(reduce_fun.lstrip('\n\r'))

        self.map_fun = dedent(map_fun.lstrip('\n\r'))
        self.reduce_fun = reduce_fun
        self.language = language

    def __repr__(self) -> str:
        return '<%s %r %r>' % (type(self).__name__, self.map_fun,
                               self.reduce_fun)

    def _exec(self, doc_ids:list, options:dict) -> dict:
        json = {'map': self.map_fun, 'language': self.language}
        if self.reduce_fun: json['reduce'] = self.reduce_fun
        if doc_ids: json['keys'] = doc_ids
        else: assert "keys" not in options, "for keys, set the doc_ids list"
        response = self.resource.postJson(json=json, chunked=True,
                                          **EncodeViewOptions(options))
        if isinstance(response.data, network.ResponseStream):
            # temp view data cannot be iterated, because the last element is
            # not cleanly separated from the entire rest
            # TODO: check if CouchDB finally fixed streaming of TempView data
            raw = response.data.read()
            return serializer.Decode(raw.decode(response.charset))
        else:
            return response.data


class Database(object):
    """
    Representation of a database on a CouchDB server.

    >>> server = Server()
    >>> db = server.create('python-tests')

    New documents can be added to the database using the `save()` method:

    >>> doc_id, doc_rev = db.save({'type': 'Person', 'name': 'John Doe'})

    This class provides a dictionary-like interface to databases: documents are
    retrieved by their ID using item access

    >>> doc = db[doc_id]
    >>> doc                 #doctest: +ELLIPSIS
    <Document ...@...>

    Documents are represented as instances of the `Row` class, which is
    basically just a normal dictionary with the additional attributes ``id`` and
    ``rev``:

    >>> doc.id, doc.rev     #doctest: +ELLIPSIS
    ('...', ...)
    >>> doc['type']
    'Person'
    >>> doc['name']
    'John Doe'

    To update an existing document, you use item access, too:

    >>> doc['name'] = 'Mary Jane'
    >>> db[doc.id] = doc

    The `save()` method creates a document with a random ID generated by
    CouchDB (which is not recommended). If you want to explicitly specify the
    ID, you'd use item access just as with updating:

    >>> db['JohnDoe'] = {'type': 'person', 'name': 'John Doe'}
    >>> 'JohnDoe' in db
    True
    >>> len(db)
    2

    >>> del server['python-tests']
    """

    def __init__(self, url, name:str=None, session:network.Session=None):
        """
        :param url: A URL or the path of the DB as `str` or a
                    :class:`.network.Resource`; a URL must be fully qualified,
                    including the scheme (http[s]), otherwise the
                    :data:`.COUCHDB_URL` is prepended to the *url*.
        :param name: The name of the DB, usually set automagically.
        :param session: A :class:`.network.Session` object; if ``None``, a new
                        `Session` is created.
        """
        if isinstance(url, str):
            if not url.startswith('http'):
                url = COUCHDB_URL + url
            self.resource = network.Resource(url, session)
        else:
            self.resource = url
        self._name = name

    def __repr__(self) -> str:
        return '<%s %r>' % (type(self).__name__, self.name)

    def __hash__(self):
        return "{} {}".format(self.resource.url, self.resource.credentials)

    def __contains__(self, id:str) -> bool:
        """
        Return ``True`` if the DB contains a document with the specified ID.
        """
        try:
            self.resource.head(*DocPath(id))
            return True
        except network.ResourceNotFound:
            return False

    def __iter__(self) -> iter([str]):
        """
        Return the ID strings of all documents in the DB.
        """
        return (item.id for item in self.view('_all_docs'))

    def __len__(self) -> int:
        """
        Return the number of documents in the DB.
        """
        response = self.resource.getJson()
        return int(response.data['doc_count'])

    def __bool__(self) -> bool:
        """
        Return ``True`` iff the DB is available.
        """
        try:
            self.resource.head()
            return True
        except:
            return False

    def __delitem__(self, id:str):
        """
        Remove the document with the specified *ID* from the database.

        :raise network.ResourceNotFound: If no such document exists.
        """
        path = DocPath(id)
        response = self.resource.head(*path)
        self.resource.delete(*path, rev=response.headers['etag'].strip('"'))

    def __getitem__(self, id:str) -> Document:
        """
        Return the :class:`.Document` with the specified *ID*.

        :raise network.ResourceNotFound: If no such document exists.
        """
        response = self.resource.getJson(*DocPath(id))
        return Document(response.data)

    def __setitem__(self, id:str, document:dict):
        """
        Create or update a *document* with the specified *ID*.

        :param document: The document; either a plain dictionary (even without
            ``_id`` or ``_rev`` values), or a :class:`.Document`.
        :raise fnl.couch.network.ResourceConflict: If the document's
            revision value does not match the value in the DB.
        """
        old_stamps = SetTimestamps(document)

        try:
            response = self.resource.putJson(*DocPath(id), json=document)
        except:
            UnsetTimestamps(document, old_stamps)
            raise

        document['_id']  = response.data['id']
        document['_rev'] = response.data['rev']

    # DATABASE API

    @property
    def name(self) -> str:
        """
        The name string of the database, unescaped.

        Note that this may trigger a request to the server unless the name has
        already been cached by :meth:`.info`.
        """
        if self._name is None: self.info()
        return self._name

    @property
    def revs_limit(self) -> int:
        """
        Get the revision limit of the database.
        """
        return self.resource.getJson('_revs_limit').data

    @revs_limit.setter
    def revs_limit(self, revs_limit:int):
        """
        Set the revision limit of the database.
        """
        response = self.resource.putJson('_revs_limit', json=revs_limit)
        assert response.data['ok']

    def cleanup(self) -> bool:
        """
        Clean up old design document indexes (aka. "view cleanup").

        Removes all unused index files from the database storage area.

        :return: A `bool` to indicate successful cleanup **initiation**.
        """
        response = self.resource.postJson('_view_cleanup')
        return response.data['ok']

    def commit(self) -> bool:
        """
        If the server is configured to delay commits, or previous requests
        used the special ``X-Couch-Full-Commit: false`` header to disable
        immediate commits, this method can be used to ensure that any
        non-committed changes are committed to physical storage.

        :return: A `bool` indicating success.
        """
        response = self.resource.postJson('_ensure_full_commit')
        return response.data['ok']

    def compact(self, ddoc:str=None) -> bool:
        """
        Compact the database or a design document's (view) index.

        Without an argument, this will try to prune all old revisions from the
        database. With an argument, it will compact the index cache for all
        views in the design document specified.

        :return: A `bool` to indicate whether the compaction was **initiated**
            successfully.
        """
        if ddoc: response = self.resource.postJson('_compact', ddoc)
        else: response = self.resource.postJson('_compact')

        return response.data.get('ok', False)

    def info(self, ddoc:str=None) -> dict:
        """
        Return information about the database or design document as a
        dictionary.

        Without an argument, returns database information. With an argument,
        return the dictionary for the given design document.

        Design document information is found at:

        /**db**/_design/**design-doc**/_info

        :param ddoc: The name of the ``design-doc``.
        :rtype: `dict`
        """
        if ddoc is not None:
            response = self.resource.getJson('_design', ddoc, '_info')
        else:
            response = self.resource.getJson()
            self._name = response.data['db_name']

        return response.data

    # DOCUMENT API

    def save(self, document:dict, **options) -> (str, str):
        """
        **Create** a new document or **update** an existing document.

        If *document* has no ``"_id"`` then the server will allocate a random
        ID and a new document will be created. Otherwise the document's ID will
        be used to identity the document to create or update. Trying to update
        an existing document with an incorrect ``"_rev"`` will raise a
        :exc:`.network.ResourceConflict` exception.

        Note that it is generally better to avoid saving documents with no ID
        and instead generate document IDs on the client side. This is due to
        the fact that the underlying HTTP POST method is not idempotent,
        and an automatic retry due to a problem somewhere on the networking
        stack may cause multiple documents being created in the database. Even
        more, proxies and other network intermediaries will occasionally
        resend POST requests, which can result in duplicate document
        creation.

        To avoid such problems you can generate a UUID on the client side, or
        request one from the server via :meth:`.Server.uuids`. Python (since
        version 2.5) comes with a ``uuid`` module that can be used for this::

            from uuid import uuid4
            document = {'_id': uuid4().hex, 'type': 'person', 'name': 'John Doe'}
            db.save(document)

        :param document: The document to store, as `dict` or `Document`.
        :param options: Optional args, especially ``batch='ok'`` to just send
            documents to memory to achieve higher throughput. To flush the
            memory immediately, use :meth:`.Database.commit()`. Be aware that
            ``batch`` is not a safe approach and should never be used for
            critical data.
        :return: A `tuple` of the updated ``(id, rev)`` values of the document.
        :raise fnl.couch.network.ResourceConflict: If the document's
            revision value does not match the value in the DB.
        """
        if '_id' in document:
            request = DocResource(self.resource, document['_id']).putJson
        else:
            request = self.resource.postJson

        old_stamps = SetTimestamps(document)

        try:
            response = request(json=document, **options)
        except:
            UnsetTimestamps(document, old_stamps)
            raise

        id, rev = response.data['id'], response.data.get('rev')
        document['_id'] = id

        if rev is not None:  # Not present for batch='ok'
            document['_rev'] = rev

        return id, rev

    def copy(self, src, dest) -> str:
        """
        **Copy** a document to a new document or overwrite an existing one.

        :param src: The ID of the document to copy, or a dictionary or
                    `Document` object representing the source document.
        :param dest: Either a unused destination document ID as string, or a
                     dictionary or `Document` instance of the document that
                     should be overwritten with its revision value.
        :return: The new revision of the destination document.
        """
        if not isinstance(src, str):
            src = src['_id']

        if not isinstance(dest, str):
            if '_rev' in dest:
                dest = '{}?{}'.format(quote(dest['_id']),
                                      urlencode({'rev': dest['_rev']}))
            else:
                dest = quote(dest['_id'])

        response = self.resource._request('COPY', DocPath(src),
                                          headers={'Destination': dest})

        data = serializer.Decode(response.data.decode(response.charset))
        return data['rev']

    def delete(self, doc:dict):
        """
        Delete the given document from the database.

        Use this method in preference over ``__del__`` to ensure you're
        deleting the revision that you had previously retrieved. In the case
        the document has been updated since it was retrieved, this method will
        raise a `ResourceConflict`.

        >>> server = Server()
        >>> db = server.create('python-tests')

        >>> doc = dict(type='Person', name='John Doe')
        >>> db['johndoe'] = doc
        >>> doc2 = db['johndoe']
        >>> doc2['age'] = 42
        >>> db['johndoe'] = doc2
        >>> db.delete(doc)
        Traceback (most recent call last):
          ...
        fnl.couch.network.ResourceConflict: conflict: Document update conflict.

        >>> del db['johndoe']
        >>> del server['python-tests']

        :param doc: A dictionary or :class:`.Document` object holding the
            document data.
        :return: A `bool` indicating success.
        :raise fnl.couch.network.ResourceConflict: If the document was
            updated in the database (ie., the ``_rev`` values mismatch).
        :raise ValueError: If either ID or revision of the document are not
            set.
        """
        if doc.get('_id') is None:
            raise ValueError('document ID cannot be None')
        if '_rev' not in doc:
            raise ValueError('document revision must be set')

        result = self.resource.deleteJson(*DocPath(doc['_id']),
                                          rev=doc['_rev'])
        return result['ok']

    def get(self, id:str, default:object=None, **options) -> Document:
        """
        Return the document with the specified ID.

        The following options are available:

         * ``full=True``: Return document incl. metadata (default ``False``).
         * ``revs=True``: Add the list of all document revision values for the
                          document (under ``_revisions``; see below). Use
                          ``revs_info=True`` for even more information - a
                          list of dictionaries with a ``rev`` and a ``status``
                          key, where the latter can have values such as
                          "disk", "missing", or "deleted".
         * ``rev="some_rev"``: Get the document, but as it was at the
                               specified revision value.
         * ``attachments=True``: Add the actual attachments, in Base64
                                 encoding.
         * ``open_revs=...``: Get multiple document revision states, e.g.,
                              after a failed strict `bulk()` use ``'all'``;
                              Otherwise, use ``['rev1', 'rev2', ...]`` to
                              specifiy exactly which. Returns a list of
                              dictionaries, either ``{'missing': 'revX'}`` or
                              ``{'ok': {THE_DOCUMENT}}``.
         * ``conflicts=True``: Add the ``_conflicts`` key listing conflicting
                               revisions of the document.

        Note that to create a real document revision value from ``_revisions``,
        you must join the revision values found in ``['_revisions']['ids']``
        with a counter that can be calculated via the value from
        ``['_revisions']['start']``::

            ids   = doc['_revisions']['ids']
            start = doc['_revisions']['start']
            revs  = [ "{}-{}".format(start - num, rev)
                      for num, rev in enumerate(ids)   ]

        ``_revs_info`` ``rev`` values do not contain this counter value either.

        :param id: The document ID.
        :param default: The default value to return when the document is not
                        found; ``None``.
        :return: A :class:`.Document` object representing the requested
                 document, or *default* if no document with the ID was found.
        """
        try:
            response = self.resource.getJson(*DocPath(id), **options)
        except network.ResourceNotFound:
            return default

        if isinstance(response.data, dict):
            return Document(response.data)
        else:
            return response.data

    def rev(self, id:str) -> str:
        """
        Get the latest revision for the given document ID.

        :param id: The document ID.
        :return: The document's revision string (_rev).
        """
        response = self.resource.head(*DocPath(id))
        return response.headers["ETag"].strip('"')

    def revisions(self, id:str, **options) -> iter([Document]):
        """
        Iterate over all available revisions of a document *ID*.

        The following options are available:

         * ``full=True``: Return documents incl. metadata (default ``False``).
         * ``attachments=True``: Add the actual attachments, in Base64
                                 encoding.

        :param id: The document ID.
        :return: An iterator over `Document` objects, each a different
                 revision, in reverse chronological order (newest first).
        """
        options["revs"] = True
        doc = self.get(id, **options)

        if doc is None:
            raise network.ResourceNotFound("document {}".format(id))

        revisions = doc['_revisions']
        del doc['_revisions']
        num_revs = revisions['start']
        del options['revs']

        yield doc

        for index, rev in enumerate(revisions['ids']):
            if not index: continue
            options['rev'] = '{}-{}'.format(num_revs - index, rev)
            revision = self.get(id, **options)

            if revision is None:
                raise network.ResourceNotFound(
                        "doc {}@{}".format(id, options['rev'])
                )

            yield revision

    # ATTACHMENT API

    def deleteAttachment(self, id_or_doc, filename:str) -> bool:
        """
        Delete the specified attachment.

        Note that the provided `doc` is required to have a ``_rev`` field.
        Thus, if the `doc` is based on a view row, the view row would need to
        include the ``_rev`` field. The document's ``_rev`` field will be
        automatically updated.

        :param id_or_doc: Either a document ID, a dictionary or a `Document`
            object representing the document that the attachment belongs to.
        :param filename: The name of the attachment file.
        :return: A `bool` indicating success.
        :raise network.ResourceNotFound: If no such document or attachment
            exists.
        """
        if isinstance(id_or_doc, str):
            doc_id = id_or_doc
            path = DocPath(doc_id)
            resp = self.resource.head(*path)
            rev = resp.headers["ETag"].strip('"')
        else:
            doc_id = id_or_doc['_id']
            path = DocPath(doc_id)
            rev = id_or_doc['_rev']

        path.append(filename)
        response = self.resource.deleteJson(*path, rev=rev)

        if isinstance(id_or_doc, dict):
            id_or_doc['_rev'] = response.data['rev']

        return response.data['ok']

    def getAttachment(self, id_or_doc, filename:str,
                      default=None) -> Attachment:
        """
        Return an attachment from the specified document *ID or doc* itself
        and *filename*, as a tuple of the response header's ``Content-Type``
        string and a file-like object.

        :param id_or_doc: Either a document ID, a dictionary or a `Document`
            object representing the document that the attachment belongs to.
        :param filename: The name of the attachment file.
        :param default: A default value to return when the document or
            attachment is not found.
        :return: An :class:`.Attachment`\, possibly streamed.
        """
        if isinstance(id_or_doc, str):
            id = id_or_doc
        else:
            id = id_or_doc['_id']

        path = DocPath(id)
        path.append(filename)

        try:
            response = self.resource.get(*path, chunked=True)
            ctype = response.headers.get('Content-Type')

            if isinstance(response.data, network.ResponseStream):
                return Attachment(ctype, response.charset, None, response.data)
            else:
                return Attachment(ctype, response.charset, response.data, None)
        except network.ResourceNotFound:
            return default

    def saveAttachment(self, id_or_doc, content, filename:str=None,
                       content_type:str=None, charset:str=None) -> Document:
        """
        Create or replace an attachment, thereby creating or updating a
        document.

        If the *content* is a `bytes` object, and the content is text, it is
        highly recommended to set the charset value, too. Otherwise, or if
        *content* is a `str` object or a :class:`io.TextIOBase` instance, it
        will be assumed the content uses **Latin-1** encoding (the default
        HTTP 1.1 encoding).

        If *id_or_doc* is a dictionary or `Document` and has no ``_rev`` field,
        an empty document with the given ``_id`` will be created. Note that the
        document itself will **not** be saved, only the empty document created.
        After the attachment has been saved, the document in memory will be
        outdated, and if it is to be used further, has to be fetched again
        from the database to obtain the CouchDB attachment information.

        For an ID in *id_or_doc*, if it exists in the DB, the ``_rev`` is
        fetched; Otherwise, the broker will attempt to create an empty document
        with that ID.

        :param id_or_doc: The dictionary, `Document`, or simply document ID
            string where the attachment should be added.
        :param content: The content to upload, either a file-like object, a
            `bytes`, or a `str` object.
        :param filename: The name of the attachment file to create/replace; if
            omitted, this method tries to get the filename from the `name`
            attribute (eg., file-like objects) of the *content* object.
        :param content_type: MIME type of the attachment; if omitted, it is
            guessed based on the *filename* extension.
        :param charset: Appended to the *content type* value, **if not defined
            there already**. If omitted and not defined in the *content type*,
            but the *content* object has an `encoding` attribute, that is
            used in stead. Note that **string** type *content* is encoded to
            **Latin-1** and no (other) *charset* should be set.
        :return: The document's ``(id, rev)`` tuple.
        """
        # determine the filename or raise exception
        if filename is None:
            if hasattr(content, 'name'):
                filename = os.path.basename(content.name)
            else:
                raise ValueError('no filename specified for attachment')

        # determine the content type or fail assertion
        if content_type is None:
            content_type = '; '.join(
                [mime for mime in mimetypes.guess_type(filename) if mime]
            )

            if not content_type and (isinstance(content, str) or
                                     isinstance(content, TextIOBase)):
                content_type = 'text/plain'

            assert content_type, \
                "could not guess MIME type of {}".format(filename)

        # append the charset to the content type if possible
        if "charset" not in content_type:
            if charset:
                content_type += "; charset={}".format(charset.lower())
            elif hasattr(content, 'encoding') and content.encoding:
                content_type += "; charset={}".format(content.encoding)
            elif isinstance(content, str) or isinstance(content, TextIOBase):
                content_type += "; charset=iso-8859-1"

        # determine the document's revision (if any)
        if isinstance(id_or_doc, str):
            doc_id = id_or_doc
            resource = self.resource(*DocPath(doc_id))

            try:
                response = resource.head()
                rev = response.headers["ETag"].strip('"')
            except (KeyError, IndexError, network.ResourceNotFound):
                rev = None
        else:
            doc_id = id_or_doc['_id']
            rev = id_or_doc.get('_rev', None)
            resource = self.resource(*DocPath(doc_id))

        # save the attachment
        response = resource.put(filename, body=content,
                                headers={'Content-Type': content_type},
                                rev=rev)
        data = serializer.Decode(response.data.decode(response.charset))
        assert data['ok']
        return data['id'], data['rev']

    # BULK DOCUMENT API

    def bulk(self, documents:[dict], strict:bool=False) -> [(bool, str, str)]:
        """
        Perform a bulk update, insertion, or deletion of the given documents
        using a single HTTP request.

        The documents inserted must have an ``_id``, and if they have the
        ``_rev`` field too, this method acts in bulk. Finally, any documents
        that have a ``_deleted`` field set to ``True`` are purged.

        >>> from fnl.couch import Document
        >>> server = Server()
        >>> db = server.create('python-tests')
        >>> for doc in db.bulk([
        ...     Document(type='Person', name='John Doe'),
        ...     Document(type='Person', name='Mary Jane'),
        ...     Document(type='City', name='Gotham City')
        ... ]):
        ...     print(repr(doc)) #doctest: +ELLIPSIS
        (True, '...', '...')
        (True, '...', '...')
        (True, '...', '...')

        >>> del server['python-tests']

        The return value of this method is a `list` containing a `tuple` for
        every element in the *documents* iterable. Each `tuple` is of the form
        ``(success, docid, rev_or_exc)``, where ``success`` is a `bool`
        indicating whether the change succeeded, ``docid`` is the ID of the
        document, and ``rev_or_exc`` is either the new document revision, or
        an exception instance (e.g. `ResourceConflict`) if the change failed.

        :param documents: A sequence of dictionaries or `Document` objects.
        :param strict: If all changes must succeed for any change to happen
                       (aka ``'all_or_nothing': true``}
        :return: A `list` of (`bool`, `str`, `str`) `tuples`.
        """
        documents = list(documents)
        now = datetime.utcnow()
        old_stamps = [SetTimestamps(doc, now) for doc in documents]
        content = dict(docs=documents)
        if strict: content['all_or_nothing'] = True
        # TODO: would it pay off making this a chunked request?
        response = self.resource.postJson('_bulk_docs', json=content)
        results = []

        for idx, result in enumerate(response.data):
            if 'error' in result:
                if result['error'] == 'conflict':
                    exc_type = network.ResourceConflict
                else:
                    # XXX: Any other error types mappable to exceptions here?
                    exc_type = network.ServerError

                UnsetTimestamps(documents[idx], old_stamps[idx])
                results.append((False, result['id'],
                                exc_type(result['reason'])))
            else:
                doc = documents[idx]
                doc['_id']  = result['id']
                doc['_rev'] = result['rev']
                results.append((True, result['id'], result['rev']))

        return results

    def purge(self, docs:[dict]) -> (int, dict):
        """
        Perform bulk purging (complete removing) of the given documents.

        Uses a single HTTP request to purge all given documents. Purged
        documents do not leave any metadata in the storage and are not
        replicated.

        :return: A tuple containing the ``purge_seq`` integer and
                 ``purged`` dictionary.
        """
        content = { doc['_id']: [doc['_rev']] for doc in docs }
        response = self.resource.postJson('_purge', json=content)
        return response.data['purge_seq'], response.data['purged']

    # VIEW API

    def query(self, map_fun, reduce_fun=None, language:str='javascript',
              doc_ids:[str]=None, wrapper=Row, **options) -> ViewResults:
        """
        Execute an ad-hoc query (a "temporary view") against the database.

        >>> server = Server()
        >>> db = server.create('python-tests')
        >>> db['johndoe'] = dict(type='Person', name='John Doe')
        >>> db['maryjane'] = dict(type='Person', name='Mary Jane')
        >>> db['gotham'] = dict(type='City', name='Gotham City')
        >>> map_fun = '''function(doc) {
        ...     if (doc.type == 'Person')
        ...         emit(doc.name, null);
        ... }'''
        >>> for row in db.query(map_fun):
        ...     print(row.key)
        John Doe
        Mary Jane

        >>> for row in db.query(map_fun, descending=True):
        ...     print(row.key)
        Mary Jane
        John Doe

        >>> for row in db.query(map_fun, key='John Doe'):
        ...     print(row.key)
        John Doe

        >>> del server['python-tests']

        View functions are made available at:

            /\ **db**\ /\ _temp_view

        Temporary view request options:

         * ``descending=True`` Order descending.
         * ``include_docs=True`` Include the full documents.
         * ``limit=<int>`` Limit result to ``<int>`` number of documents.
         * ``startkey='...'`` Start key to return documents in range.
         * ``endkey='...'`` End key to return documents in range.
         * ``startkey_docid='...'`` Start range with specific doc ID.
         * ``endkey_docid='...'`` End range with specific doc ID.
         * ``key='...'`` Only display documents that matches the key.
         * ``stale='ok'`` Don't refresh view for quicker results.
         * ``skip=<int>`` Skip the first ``<int>`` documents.
         * ``group=True`` Group results.
         * ``group_level=<int>`` Level at which documents should be grouped.
         * ``reduce=False`` If given, do not use the reduce function.

        :param map_fun: The code of the map function.
        :param reduce_fun: The code of the reduce function (optional).
        :param language: The language of the functions, to determine which view
                         server to use.
        :param doc_ids: A list of document IDs to limit the query to.
        :param wrapper: An optional callable that should be used to wrap the
                        result rows (defaults to :class:`.Row`).
        :param options: Optional query parameters.
        :return: The query's results.
        :rtype: :class:`.ViewResults`
        """
        return TemporaryView(self.resource('_temp_view'), map_fun,
                             reduce_fun, language=language,
                             wrapper=wrapper)(doc_ids, **options)

    def view(self, name, doc_ids:[str]=None, wrapper=Row,
             **options) -> ViewResults:
        """
        Execute a predefined view:

        >>> server = Server()
        >>> db = server.create('python-tests')
        >>> db['gotham'] = dict(type='City', name='Gotham City')

        >>> for row in db.view('_all_docs'):
        ...     print(row.id)
        gotham

        >>> del server['python-tests']

        View functions are made available at:

            /\ **db**\ /\ _design/\ **design-doc**\ /\ _view/\ **view-name**

        Permanent view request options:

         * ``descending=True`` Order descending.
         * ``include_docs=True`` Include the full documents.
         * ``limit=<int>`` Limit result to ``<int>`` number of documents.
         * ``startkey='...'`` Start key to return documents in range.
         * ``endkey='...'`` End key to return documents in range.
         * ``startkey_docid='...'`` Start range with specific doc ID.
         * ``endkey_docid='...'`` End range with specific doc ID.
         * ``key='...'`` Only display documents that matches the key.
         * ``stale='ok'`` Don't refresh view for quicker results.
         * ``skip=<int>`` Skip the first ``<int>`` documents.
         * ``group=True`` Group results.
         * ``group_level=<int>`` Level at which documents should be grouped.
         * ``reduce=False`` If exists, do not use the reduce function.

        :param name: The name of the view; for custom views, use the format
                     ``design-doc/view-name``, that is, the document ID of the
                     design document and the name of the view, separated by a
                     slash.
        :param doc_ids: A list of document IDs to limit the view to.
        :param wrapper: An optional callable that should be used to wrap the
                        result rows (defaults to :class:`.Row`).
        :param options: Optional query parameters.
        :return: The view's results.
        :rtype: :class:`.ViewResults`
        """
        path = DesignPathFromName(name, '_view')
        return PermanentView(self.resource(*path), '/'.join(path),
                             wrapper=wrapper)(doc_ids, **options)

    def show(self, name:str, doc_id:str=None, **options) -> network.Response:
        """
        Call a **show function**, returning the result produce by it.

        Show functions are made available at:

            /\ **db**\ /\ _design/\ **design-doc**\ /\ _show/\ **show-name**\ [/\ **doc-id**\ ]

        Show request *options*:

         * ``format='...'`` File format to show the document it.
         * ``details=True`` Show document details.

        :param name: The name of the show handler in the format
            ``design-doc/show-name``.
        :param doc_id: Optional ID of a document to pass to the show function.
        :param options: Optional query parameters.
        :return: A :class:`.network.Response` named tuple, where ``data`` is a
            `bytes` object or a :class:`.network.ResponseStream`.
        """
        path = DesignPathFromName(name, '_show')
        if doc_id: path.append(doc_id)
        return self.resource.get(*path, chunked=True, **options)

    def list(self, name:str, view:str, doc_ids:[str]=None,
             **options:{str:object}) -> network.Response:
        """
        Format a view using a **list function**, returning the result produced
        by it.

        List functions are made available at:

            /\ **db**\ /\ _design/\ **design-doc**\ /\ _list/\ **list-name**\ /[\ **other-design-doc/**\ ]\ **view-name**

        List handler request options:

         * ``descending=True`` Order descending.
         * ``include_docs=True`` Include the full documents.
         * ``limit=<int>`` Limit result to ``<int>`` number of documents.
         * ``startkey='...'`` Start key to return documents in range.
         * ``endkey='...'`` End key to return documents in range.
         * ``startkey_docid='...'`` Start range with specific doc ID.
         * ``endkey_docid='...'`` End range with specific doc ID.
         * ``key='...'`` Only display documents that matches the key.
         * ``stale='ok'`` Don't refresh view for quicker results.
         * ``skip=<int>`` Skip the first ``<int>`` documents.
         * ``group=True`` Group results.
         * ``group_level=<int>`` Level at which documents should be grouped.
         * ``reduce=False`` If exists, do not use the reduce function.

        :param name: The name of the list function in the format
             ``design-doc/list-name``.
        :param view: The name of the view in the format
             ``other-design-doc/view-name`` or just ``view-name``.
        :param doc_ids: A list of document IDs to limit the list function to.
        :param options: Optional query parameters.
        :return: A :class:`.network.Response` named tuple, where ``data`` is
             an `iter`\ able and :class:`.network.ResponseStream`\ .
        """
        path = DesignPathFromName(name, '_list')
        path.extend(view.split('/', 1))
        response = CallViewlike(self.resource(*path), doc_ids, options)
        return response

    def update(self, name:str, doc_id:str=None,
               **options:dict) -> network.Response:
        """
        Calls the server-side update handler.

        Update handlers are functions that clients can request to invoke
        server-side logic that will create or update a document, eg, to provide
        a server-side last modified timestamp, updating individual fields in a
        document without first getting the latest revision, etc..

        Update handlers are made available at:

            /\ **db**\ /\ _design/\ **design-doc**\ /\ _update/\ **update-name**\ [/\ **doc-id**\ ]

        Update handlers options:

        TODO: list options

        :param name: The name of the update handler function in the format
                     ``design-doc/update-name``.
        :param doc_id: Optional ID of a document to pass to the update handler.
        :param options: Optional query parameters.
        :return: A :class:`.network.Response` named tuple, where ``data`` is
                 string containing the output as defined by the update
                 function.
        """
        path = DesignPathFromName(name, '_update')

        if doc_id is None:
            request = self.resource.post
        else:
            path.append(doc_id)
            request = self.resource.put

        return request(*path, **options)

    # CHANGES API

    def changes(self, **opts:{str:object}) -> dict:
        """
        Retrieve a changes feed from the database.

        The options available are documented at `CouchDB HTTP API - Changes
        <http://wiki.apache.org/couchdb/HTTP_database_API#Changes>`_

        A **changes dictionary** has the following fields:

         * ``seq`` -- The sequence number of the particular change.
         * ``id`` -- The ID of the changed document.
         * ``changes``-- A list of revisions as: ``[{"rev": "...."}]``.
         * ``deleted`` -- Only present, with a value of ``True``, if the
           document was deleted.

        :return: A ``(last_seq, results)`` tuple, where *results* is a list of
            changes dictionaries, and *last_seq* is an `int` defining the
            sequence number of the last change returned (normally, the last
            item in the results).  If the option ``feed='continuous'`` is used,
            instead an iterator is returned that yields one changes dictionary
            at a time until the server stream breaks or the process is aborted.
        """
        if opts.get('feed') == 'continuous':
            return self._streamingChanges(**opts)

        response = self.resource.getJson('_changes', **opts)
        return response.data['last_seq'], response.data['results']

    def _streamingChanges(self, **opts:{str:object}):
        response = self.resource.get('_changes', chunked=True, **opts)
        assert isinstance(response.data, network.ResponseStream)
        stream = iter(response.data)

        for raw in stream:
            line = raw.decode(response.charset)
            if 'last_seq' in line:
                response.data.close()

            try:
                yield serializer.Decode(line)
            except ValueError:
                raise ValueError('not valid JSON: "{}"'.format(line))


class Server:
    """
    Representation of a CouchDB server.

    >>> server = Server()

    This class behaves like a dictionary of databases. For example, to get a
    list of database names on the server, you can simply iterate over the
    server object.

    New databases can be created using the `create` method:

    >>> db = server.create('python-tests')
    >>> db
    <Database 'python-tests'>

    You can access existing databases using item access, specifying the
    database name as the key:

    >>> db = server['python-tests']
    >>> db.name
    'python-tests'

    Databases can be deleted using a ``del`` statement:

    >>> del server['python-tests']
    """

    def __init__(self, url:object=COUCHDB_URL, full_commit:bool=True,
                 session:network.Session=None):
        """
        :param url: The URL of the server (for example
                    ``http://localhost:5984/``) or a
                    :class:`.network.Resource` instance.
        :param full_commit: Turns on the ``X-Couch-Full-Commit`` header.
        :param session: An :class:`.network.Session` instance.
        """
        if isinstance(url, str):
            self.resource = network.Resource(url, session)
        else:
            self.resource = url  # treat as a Resource object
        if not full_commit:
            self.resource.headers['X-Couch-Full-Commit'] = 'false'

    def __hash__(self) -> str:
        return "{} {}".format(self.resource.url, self.resource.credentials)

    def __repr__(self) -> str:
        return '<%s %r>' % (type(self).__name__, self.resource.url)

    def __contains__(self, name:str) -> bool:
        """
        Return ``True`` if the server contains a database with the specified
        *name*, ``False`` otherwise.

        :raise TypeError: If the DB *name* is not a string.
        :raise ValueError: If the DB *name* evaluates to ``False`` or is not
            valid.
        """
        try:
            self.resource.head(ValidateDbName(name))
            return True
        except network.ResourceNotFound:
            return False
        except ValueError:
            return False

    def __iter__(self) -> iter([str]):
        """
        Iterate over the names of all databases.
        """
        response = self.resource.getJson('_all_dbs')
        return iter(response.data)

    def __len__(self) -> int:
        """
        Return the number of databases.
        """
        response = self.resource.getJson('_all_dbs')
        return len(response.data)

    def __bool__(self) -> bool:
        """
        ``True`` if the server is available.
        """
        try:
            self.resource.head()
            return True
        except:
            return False

    def __delitem__(self, name:str):
        """
        Remove the database with the specified *name*.

        :raise fnl.couch.network.ResourceNotFound: If no database with that
            *name* exists.
        :raise TypeError: If the DB *name* is not a string.
        :raise ValueError: If the DB *name* evaluates to ``False`` or is not
            valid.
        """
        self.resource.delete(ValidateDbName(name))

    def __getitem__(self, name:str) -> Database:
        """
        Return a :class:`.Database` object representing the database with the
        specified *name*. Creates the DB if it does not exist.

        :raise TypeError: If the DB *name* is not a string.
        :raise ValueError: If the DB *name* evaluates to ``False`` or is not
            valid.
        """
        name = ValidateDbName(name)
        db = Database(self.resource(name), name)

        try:
            db.resource.head()  # actually make a request to the database
            return db
        except network.ResourceNotFound:
            return self.create(name)

    def config(self, section:str=None) -> dict:
        """
        The configuration of the CouchDB server.

        The configuration is represented as a nested dictionary of sections and
        options from the configuration files of the server, or the default
        values for options that are not explicitly configured.

        Sections:

         * ``attachments``
         * ``couch_httpd_auth``
         * ``couchdb``
         * ``daemons``
         * ``httpd``
         * ``httpd_db_handlers``
         * ``httpd_design_handlers``
         * ``httpd_global_handlers``
         * ``log``
         * ``query_server_config``
         * ``query_servers``
         * ``replicator``
         * ``stats``
         * ``uuids``

        :param section: Only return the configuration for that section, or
                        a ``section/key`` to return only a specific value.
        """
        if section:
            response = self.resource.getJson('_config', *section.split('/', 1))
        else:
            response = self.resource.getJson('_config')

        return response.data

    def version(self) -> str:
        """
        The version string of the CouchDB server.
        """
        response = self.resource.getJson()
        return response.data['version']

    def restart(self) -> bool:
        """
        Restart the CouchDB.
        """
        response = self.resource.postJson('_restart')
        return response.data['ok']

    def stats(self, name:str=None) -> dict:
        """
        Server statistics.

        First part of *name* should be:

         * ``couchdb``
         * ``httpd``
         * ``httpd_request_methods``
         * ``httpd_status_codes``

        :param name: Name of single statistic separated by a slash, e.g.
            ``"httpd/requests"`` (``None`` -- return all statistics).
        :return: The dictionary of statistics, one key per first part of
            *name* (even if *name* was not ``None``).
        :raise ValueError: If the *name* isn't separable into two parts.
        """
        if name:
            response = self.resource.getJson('_stats', *name.split('/', 1))
        else:
            response = self.resource.getJson('_stats')

        return response.data

    def tasks(self) -> list:
        """
        A list of tasks currently active on the server.
        """
        response = self.resource.getJson('_active_tasks')
        return response.data

    def uuids(self, count:int=None) -> [str]:
        """
        Retrieve a list of uuids.

        :param count: The number of uuids to fetch
                      (``None`` -- get as many as the server sends).
        """
        response = self.resource.getJson('_uuids', count=count)
        return response.data['uuids']

    def create(self, name:str) -> Database:
        """
        Create and return a new :class:`.Database` with the given name.

        :raise fnl.couch.network.PreconditionFailed: If a database with
            that *name* already exists.
        :raise TypeError: If the DB *name* is not a string.
        :raise ValueError: If the DB *name* evaluates to ``False`` or is not
            valid.
        """
        self.resource.putJson(ValidateDbName(name))
        return self[name]

    def delete(self, name:str):
        """
        Delete the database with the specified *name*.

        Same as ``del server[name]``.

        :raise fnl.couch.network.ResourceNotFound: If no database with that
            *name* exists.
        """
        del self[name]

    def replicate(self, source:str, target:str, **options) -> dict:
        """
        Replicate changes from the database URL *source* to the database
        URL *target*.

        Options:

         * ``cancel=True``: Cancel replication.
         * ``continuous=True``: Activate continuous replication. Use both
           cancel and continuous to cancel continuous replication.
         * ``filter="mydoc/myfilter"``: Activate filtered replication.
         * ``doc_ids=["foo", "bar", "baz"]``: Replicate the specified
           Documents.
         * ``proxy="http://localhost:8888``: Replicate through a proxy.

        :param source: URL of the source database.
        :param target: URL of the target database.
        :param options: Optional replication arguments.
        """
        data = {'source': source, 'target': target}
        data.update(options)
        response = self.resource.postJson('_replicate', json=data)
        return response.data
