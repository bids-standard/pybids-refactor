import os
import re
import json
from keyword import iskeyword
from collections import namedtuple
import warnings
from copy import deepcopy

from bids.utils import listify
from bids.config import get_option
from bids.external import six


__all__ = [
    "Config",
    "Entity",
    "BIDSFile",
    "BIDSRootNode",
    "BIDSSubjectNode",
    "BIDSSessionNode"
]


class Config(object):

    def __init__(self, name, entities=None, default_path_patterns=None):

        self.name = name
        self.entities = {}
        self.default_path_patterns = default_path_patterns

        for ent in entities:
            self.entities[ent['name']] = Entity(**ent)

    @classmethod
    def load(self, config):
        if isinstance(config, six.string_types):
            config_paths = get_option('config_paths')
            if config in config_paths:
                config = config_paths[config]
            if not os.path.exists(config):
                raise ValueError("{} is not a valid path.".format(config))
            else:
                with open(config, 'r') as f:
                    config = json.load(f)
        return Config(**config)


class Entity(object):

    def __init__(self, name, pattern=None, mandatory=False, directory=None,
                 map_func=None, dtype=None, aliases=None, **kwargs):
        """
        Represents a single entity defined in the JSON config.

        Args:
            name (str): The name of the entity (e.g., 'subject', 'run', etc.)
            pattern (str): A regex pattern used to match against file names.
                Must define at least one group, and only the first group is
                kept as the match.
            mandatory (bool): If True, every File _must_ match this entity.
            directory (str): Optional pattern defining a directory associated
                with the entity.
            map_func (callable): Optional callable used to extract the Entity's
                value from the passed string (instead of trying to match on the
                defined .pattern).
            dtype (str): The optional data type of the Entity values. Must be
                one of 'int', 'float', 'bool', or 'str'. If None, no type
                enforcement will be attempted, which means the dtype of the
                value may be unpredictable.
            kwargs (dict): Additional keyword arguments.
        """
        if pattern is None and map_func is None:
            raise ValueError("Invalid specification for Entity '%s'; no "
                             "pattern or mapping function provided. Either the"
                             " 'pattern' or the 'map_func' arguments must be "
                             "set." % name)
        self.name = name
        self.pattern = pattern
        self.mandatory = mandatory
        self.directory = directory
        self.map_func = map_func
        self.kwargs = kwargs

        if isinstance(dtype, six.string_types):
            dtype = eval(dtype)
        if dtype not in [str, float, int, bool, None]:
            raise ValueError("Invalid dtype '%s'. Must be one of int, float, "
                             "bool, or str." % dtype)
        self.dtype = dtype

        self.files = {}
        self.regex = re.compile(pattern) if pattern is not None else None

    def __iter__(self):
        for i in self.unique():
            yield(i)

    def __deepcopy__(self, memo):

        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result

        for k, v in self.__dict__.items():
            new_val = getattr(self, k) if k == 'regex' else deepcopy(v, memo)
            setattr(result, k, new_val)
        return result

    def match_file(self, f):
        """
        Determine whether the passed file matches the Entity.

        Args:
            f (File): The File instance to match against.

        Returns: the matched value if a match was found, otherwise None.
        """
        if self.map_func is not None:
            val = self.map_func(f)
        else:
            m = self.regex.search(f.path)
            val = m.group(1) if m is not None else None

        return self._astype(val)

    def add_file(self, filename, value):
        """ Adds the specified filename to tracking. """
        self.files[filename] = value

    def unique(self):
        """ Returns all unique values/levels for the current entity. """
        return list(set(self.files.values()))

    def count(self, files=False):
        """ Returns a count of unique values or files.

        Args:
            files (bool): When True, counts all files mapped to the Entity.
                When False, counts all unique values.
        Returns: an int.
        """
        return len(self.files) if files else len(self.unique())

    def _astype(self, val):
        if val is not None and self.dtype is not None:
            val = self.dtype(val)
        return val
        

class BIDSFile(object):
    """ Represents a single BIDS file. """
    def __init__(self, filename, parent=None):
        self.path = filename
        self.filename = os.path.basename(self.path)
        self.dirname = os.path.dirname(self.path)
        self.tags = []
        self.entities = {}
        self.parent = parent

    def _matches(self, entities=None, extensions=None, regex_search=False):
        """
        Checks whether the file matches all of the passed entities and
        extensions.

        Args:
            entities (dict): A dictionary of entity names -> regex patterns.
            extensions (str, list): One or more file extensions to allow.s
            regex_search (bool): Whether to require exact match (False) or
                regex search (True) when comparing the query string to each
                entity.
        Returns:
            True if _all_ entities and extensions match; False otherwise.
        """
        if extensions is not None:
            if isinstance(extensions, six.string_types):
                extensions = [extensions]
            extensions = '(' + '|'.join(extensions) + ')$'
            if re.search(extensions, self.filename) is None:
                return False

        if entities is not None:

            for name, val in entities.items():

                if (name not in self.entities) ^ (val is None):
                    return False

                if val is None:
                    continue

                def make_patt(x):
                    patt = '%s' % x
                    if isinstance(x, (int, float)):
                        # allow for leading zeros if a number was specified
                        # regardless of regex_search
                        patt = '0*' + patt
                    if not regex_search:
                        patt = '^%s$' % patt
                    return patt

                ent_patts = [make_patt(x) for x in listify(val)]
                patt = '|'.join(ent_patts)

                if re.search(patt, str(self.entities[name])) is None:
                    return False
        return True

    def copy(self, path_patterns, symbolic_link=False, root=None,
             conflicts='fail'):
        ''' Copy the contents of a file to a new location, with target
        filename defined by the current File's entities and the specified
        path_patterns. '''
        new_filename = build_path(self.entities, path_patterns)
        if not new_filename:
            return None

        if new_filename[-1] == os.sep:
            new_filename += self.filename

        if os.path.isabs(self.path) or root is None:
            path = self.path
        else:
            path = os.path.join(root, self.path)

        if not os.path.exists(path):
            raise ValueError("Target filename to copy/symlink (%s) doesn't "
                             "exist." % path)

        if symbolic_link:
            contents = None
            link_to = path
        else:
            with open(path, 'r') as f:
                contents = f.read()
            link_to = None

        write_contents_to_file(new_filename, contents=contents,
                               link_to=link_to, content_mode='text', root=root,
                               conflicts=conflicts)

    def __getattr__(self, attr):
        # Ensures backwards compatibility with old File_ namedtuple, which is
        # deprecated as of 0.7.
        if attr in self.entities:
            warnings.warn("Accessing entities as attributes is deprecated as "
                          "of 0.7. Please use the .entities dictionary instead"
                          " (i.e., .entities['%s'] instead of .%s."
                          % (attr, attr))
            return self.entities[attr]
        raise AttributeError("%s object has no attribute named %r" %
                             (self.__class__.__name__, attr))

    def __repr__(self):
        source = ''
        layout = self.parent.layout
        if layout.sources:
            source = ", root='{}'".format(os.path.basename(layout.root))
        return "<BIDSFile filename='{}'{}>".format(
            os.path.relpath(self.path, start=layout.root), source)

    @property
    def image(self):
        """ Return the associated image file (if it exists) as a NiBabel object
        """
        try:
            import nibabel as nb
            return nb.load(self.path)
        except Exception:
            return None

    @property
    def metadata(self):
        """ Return all associated metadata. """
        return self.layout.get_metadata(self.path)

    @property
    def layout(self):
        return self.parent.layout


class BIDSNode(object):

    _child_class = None
    _child_entity = None
    _entities = {}

    def __init__(self, path, config, root=None, parent=None):
        self.path = path
        self.config = listify(config)
        self.root = root
        self.parent = parent
        self.entities = {}
        self.available_entities = {}
        self.children = []
        self.files = []
        self.variables = []

        # Check for additional config file in directory
        layout_file = self.layout.config_filename
        config_file = os.path.join(self.abs_path, layout_file)
        if os.path.exists(config_file):
            cfg = Config.load(config_file)
            self.config.append(cfg)

        # Consolidate all entities
        self._update_entities()

        # Extract local entity values
        self._extract_entities()

        # Do subclass-specific setup
        self._setup()

        # Append to layout's master list of nodes
        self.layout.nodes.append(self)

        # Index files and create child nodes
        self.index()

    def __getitem__(self, key):
        if key in self.children:
            return self.children[key]
        if key in self.files:
            return self.files[key]
        raise AttributeError("BIDSNode at path {} has no child node or file "
                             "named {}.".format(self.path, key))
    
    def _update_entities(self):
        # Make all entities easily accessible in a single dict
        self.available_entities = {}
        for c in self.config:
            self.available_entities.update(c.entities)
    
    def _extract_entities(self):
        self.entities = {}
        for ent in self._entities:
            m = re.findall(self.available_entities[ent].pattern, self.path)
            if m:
                self.entities[ent] = m[0]

    def _get_child_class(self, path):
        ''' Return the appropriate child class given a subdirectory path '''
        if self._child_entity is None:
            return BIDSNode

        for i, child_ent in enumerate(listify(self._child_entity)):
            template = self.available_entities[child_ent].directory
            if template is None:
                return BIDSNode
            # Replace {{root}} placeholder with root node's path
            template = template.replace('{{root}}', self.root_path)
            # Construct regex search pattern from target directory template
            to_rep = re.findall(r'\{(.*?)\}', template)
            for ent in to_rep:
                patt = self.available_entities[ent].pattern
                template = template.replace('{%s}' % ent, patt)
            template += r'[^\%s]*$' % os.path.sep
            if re.match(template, path):
                return listify(self._child_class)[i]

        return BIDSNode

    def _setup(self):
        pass

    @property
    def abs_path(self):
        return os.path.join(self.root_path, self.path)

    @property
    def root_path(self):
        return self.path if self.root is None else self.root.path
    
    @property
    def layout(self):
        return self._layout if self.root is None else self.root.layout

    def index(self):

        config_list = self.config
        layout = self.layout

        for (dirpath, dirnames, filenames) in os.walk(self.path):

            # If layout configuration file exists, delete it
            layout_file = self.layout.config_filename
            if layout_file in filenames:
                filenames.remove(layout_file)
            
            for f in filenames:

                abs_fn = os.path.join(self.path, f)

                # Skip files that fail validation
                if not layout._validate_file(abs_fn):
                    continue

                bf = BIDSFile(abs_fn, self)

                # Extract entity values
                match_vals = {}
                for e in self.available_entities.values():
                    m = e.match_file(bf)
                    if m is None and e.mandatory:
                        break
                    if m is not None:
                        match_vals[e.name] = (e, m)

                # Create Entity <=> BIDSFile mappings
                if match_vals:
                    for name, (ent, val) in match_vals.items():
                        bf.entities[name] = val
                        ent.add_file(bf.path, val)

                self.files.append(bf)
                # Also add to the Layout's master list
                self.layout.files[bf.path] = bf

            root_node = self if self.root is None else self.root

            for d in dirnames:

                d = os.path.join(dirpath, d)

                # Skip directories that fail validation
                if not layout._validate_dir(d):
                    continue

                child_class = self._get_child_class(d)
                # TODO: filter the config files based on include/exclude rules
                child = child_class(d, config_list, root_node, self)
                self.children.append(child)

            # prevent subdirectory traversal
            break


class BIDSSessionNode(BIDSNode):

    _entities = {'subject', 'session'}

    def _setup(self):
        self.label = self.entities['session']


class BIDSSubjectNode(BIDSNode):

    _child_entity = 'session'
    _child_class = BIDSSessionNode
    _entities = {'subject'}

    def _setup(self):
        self.sessions = [c for c in self.children if
                         isinstance(c, BIDSSessionNode)]
        self.label = self.entities['subject']


class BIDSRootNode(BIDSNode):

    _child_entity = 'subject'
    _child_class = BIDSSubjectNode

    def __init__(self, path, config, layout):
        self._layout = layout
        super(BIDSRootNode, self).__init__(path, config)
    
    def _setup(self):
        self.subjects = {c.label: c for c in self.children if
                         isinstance(c, BIDSSubjectNode)}
