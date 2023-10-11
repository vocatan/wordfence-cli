from typing import Dict, List

from ...intel.vulnerabilities import VulnerabilityIndex, Vulnerability, \
        VulnerabilityScanner, VulnerabilityFilter
from ...api.intelligence import VulnerabilityFeedVariant
from ...util.caching import Cacheable, DURATION_ONE_DAY
from ...wordpress.site import WordpressSite
from ...wordpress.plugin import PluginLoader, Plugin
from ...wordpress.theme import ThemeLoader, Theme
from ...logging import log
from ..subcommands import Subcommand
from .reporting import VulnScanReportManager


class VulnScanSubcommand(Subcommand):

    def _load_vulnerability_index(
                self,
                variant: VulnerabilityFeedVariant
            ) -> VulnerabilityIndex:
        def initialize_vulnerability_index() -> VulnerabilityIndex:
            client = self.context.get_wfi_client()
            vulnerabilities = client.fetch_vulnerability_feed(variant)
            return VulnerabilityIndex(vulnerabilities)
        vulnerability_index = Cacheable(
                f'vulnerability_index_{variant.path}',
                initialize_vulnerability_index,
                DURATION_ONE_DAY
            )
        return vulnerability_index.get(self.cache)

    def _scan_plugins(
                self,
                plugins: List[Plugin],
                scanner: VulnerabilityScanner
            ) -> Dict[str, Vulnerability]:
        for plugin in plugins:
            log.debug(f'Plugin {plugin.slug}, version: {plugin.version}')
            scanner.scan_plugin(plugin)

    def _scan_plugin_directory(
                self,
                directory: str,
                scanner: VulnerabilityScanner
            ) -> Dict[str, Vulnerability]:
        loader = PluginLoader(directory)
        plugins = loader.load_all()
        return self._scan_plugins(plugins, scanner)

    def _scan_themes(
                self,
                themes: List[Theme],
                scanner: VulnerabilityScanner
            ) -> Dict[str, Vulnerability]:
        for theme in themes:
            log.debug(f'Theme {theme.slug}, version: {theme.version}')
            scanner.scan_theme(theme)

    def _scan_theme_directory(
                self,
                directory: str,
                scanner: VulnerabilityScanner
            ) -> Dict[str, Vulnerability]:
        loader = ThemeLoader(directory)
        themes = loader.load_all()
        return self._scan_themes(themes, scanner)

    def _scan(
                self,
                path: str,
                scanner: VulnerabilityScanner,
                check_extensions: bool = False
            ) -> Dict[str, Vulnerability]:
        site = WordpressSite(path)
        log.debug(f'Located WordPress files at {site.core_path}')
        version = site.get_version()
        log.debug(f'WordPress Core Version: {version}')
        scanner.scan_core(version)
        if check_extensions:
            self._scan_plugins(site.get_plugins(), scanner)
            self._scan_themes(site.get_themes(), scanner)

    def _output_summary(self, scanner: VulnerabilityScanner) -> None:
        vulnerability_count = scanner.get_vulnerability_count()
        affected_count = scanner.get_affected_count()
        suffix = 'y' if vulnerability_count == 1 else 'ies'
        log.info(
                f'Found {vulnerability_count} vulnerabilit{suffix} '
                f'affecting {affected_count} installation(s)'
            )

    def _initialize_filter(self) -> VulnerabilityFilter:
        excluded = set(self.config.exclude_vulnerability)
        included = set(self.config.include_vulnerability)
        return VulnerabilityFilter(
                excluded=excluded,
                included=included,
                informational=self.config.informational
            )

    def _scan_site(self, path: str, scanner: VulnerabilityScanner) -> None:
        log.info(f'Scanning site at {path}...')
        self._scan(
                path,
                scanner,
                check_extensions=True
            )

    def invoke(self) -> int:
        feed_variant = VulnerabilityFeedVariant.for_path(self.config.feed)
        report_manager = VulnScanReportManager(self.config, feed_variant)
        io_manager = report_manager.get_io_manager()
        vulnerability_index = self._load_vulnerability_index(feed_variant)
        scanner = VulnerabilityScanner(
                vulnerability_index,
                self._initialize_filter()
            )
        with report_manager.open_output_file() as output_file:
            report = report_manager.initialize_report(output_file)
            scanner.register_result_callback(report.add_result)
            for path in self.config.trailing_arguments:
                self._scan_site(path, scanner)
            if io_manager.should_read_stdin():
                reader = io_manager.get_input_reader()
                for path in reader.read_all_entries():
                    self._scan_site(path, scanner)
            for path in self.config.wordpress_path:
                log.info(f'Scanning core installation at {path}...')
                self._scan(path, scanner)
            for path in self.config.plugin_directory:
                log.info(f'Scanning plugin directory at {path}...')
                self._scan_plugin_directory(path, scanner)
            for path in self.config.theme_directory:
                log.info(f'Scanning theme directory at {path}...')
                self._scan_theme_directory(path, scanner)
            self._output_summary(scanner)
        return 0


factory = VulnScanSubcommand