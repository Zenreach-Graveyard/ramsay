# Ramsay
Ramsay is a Bazel BUILD file generator for Python 2/3 using the pyz_rules rule set.

## At a Glance
### Installation
```bash
$ python2.7 -m virtualenv env
$ . env/bin/activate
$ python2.7 -m pip install -r requirements.txt
```

### Usage
```bash
$ ln -s ramsay/ramsay.py ~/bin/ramsay
$ export PATH="$PATH:~/bin"  # if ~/bin isn't already in your path
$ cd <directory with .py files>
$ ramsay *.py > BUILD.bazel
```

# Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

Please make sure to update tests as appropriate.

# License
[BSD-3-Clause](https://opensource.org/licenses/BSD-3-Clause)
