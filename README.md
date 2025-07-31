This project generates HTML files for each channel and private discussion in a Slack workspace from a [`slackdump`](https://github.com/rusq/slackdump) archive.

# Setup

Install the dependencies using [Poetry](https://python-poetry.org/):

   ```bash
   poetry install
   ```

# Usage

To render the channels and private discussions from a `slackdump` archive, run the following command:

   ```bash
   poetry run python -m src.slackdump-render /path/to/slackdump.sqlite
   ```

The `html` directory will be created at the root of the `slackdump` archive, containing the rendered HTML files for each channel and private discussion.

If you want to render only specific channels or private discussions, you can use the `-c` option followed by the channel names:

   ```bash
   poetry run python -m src.slackdump-render /path/to/slackdump.sqlite -c channel1,channel2
   ```

# Visual Studio Code setup

To use the tasks configured in this project, you need to set the `test.dumpdir` variable in your Visual Studio Code settings. This variable should point to the directory where your Slack dump is located.
