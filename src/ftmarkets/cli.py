# Assuming we have a standard logger we want to configure somewhere,
# we can inject a simple setup_logging function here since we removed the previous CLI args parsing.
import logging
import sys

from pydantic_market_data.cli_models import GlobalArgs, PatchedCliSettingsSource
from pydantic_settings import (
    BaseSettings,
    CliApp,
    CliSubCommand,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .commands.history import HistoryCommand
from .commands.lookup import LookupCommand


def setup_logging(v: bool, vv: bool):
    if vv:
        level = logging.DEBUG
        verbosity = 2
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    elif v:
        level = logging.INFO
        verbosity = 1
        fmt = "%(name)s: %(message)s"
    else:
        level = logging.WARNING
        verbosity = 0
        fmt = "%(message)s"

    logging.basicConfig(
        level=level, 
        format=fmt, 
        datefmt="%Y-%m-%d %H:%M:%S", 
        stream=sys.stderr, 
        force=True
    )

    if verbosity < 2:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)


class AppCLI(BaseSettings, GlobalArgs):
    """Financial Times Markets CLI Tool"""

    model_config = SettingsConfigDict(
        cli_parse_args=True,
        cli_kebab_case=True,
        cli_implicit_flags="toggle",
        cli_hide_none_type=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            PatchedCliSettingsSource(settings_cls),
        )

    lookup: CliSubCommand[LookupCommand]
    history: CliSubCommand[HistoryCommand]

    def cli_cmd(self) -> None:
        v_main = self.v
        vv_main = self.vv
        
        # In pydantic-settings, the subcommand is stored in the field name
        # but CliApp might also set 'subcommand' attribute if configured or by convention.
        sub = getattr(self, "subcommand", None)
        if not sub:
            if getattr(self, "lookup", None):
                sub = self.lookup
            elif getattr(self, "history", None):
                sub = self.history
        
        v_sub = getattr(sub, "v", False) if sub else False
        vv_sub = getattr(sub, "vv", False) if sub else False
        
        v = v_main or v_sub
        vv = vv_main or vv_sub
        
        setup_logging(v, vv)
        CliApp.run_subcommand(self)


def main():
    CliApp.run(AppCLI)


if __name__ == "__main__":
    main()
