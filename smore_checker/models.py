from dataclasses import dataclass, field


@dataclass
class ImageInfo:
    src: str
    alt: str
    block_id: str
    section_name: str
    element_selector: str


@dataclass
class LinkInfo:
    href: str
    text: str
    original_href: str
    block_id: str
    section_name: str
    element_selector: str


@dataclass
class HeadingInfo:
    tag: str  # H1, H2, etc.
    text: str
    block_id: str
    section_name: str
    level: int  # 1-6


@dataclass
class EmbedInfo:
    tag: str
    src: str
    block_id: str
    section_name: str
    element_selector: str


@dataclass
class SmoreBlock:
    block_id: str
    block_type: str  # header, text.title, text.paragraph, image.single, misc.separator, etc.
    section_id: str
    text_content: str
    images: list[ImageInfo] = field(default_factory=list)
    links: list[LinkInfo] = field(default_factory=list)
    headings: list[HeadingInfo] = field(default_factory=list)
    embeds: list[EmbedInfo] = field(default_factory=list)


@dataclass
class SmoreSection:
    """A logical section: group of blocks between separators, named by text.title."""
    name: str
    blocks: list[SmoreBlock] = field(default_factory=list)

    @property
    def images(self) -> list[ImageInfo]:
        return [img for b in self.blocks for img in b.images]

    @property
    def links(self) -> list[LinkInfo]:
        return [link for b in self.blocks for link in b.links]

    @property
    def headings(self) -> list[HeadingInfo]:
        return [h for b in self.blocks for h in b.headings]

    @property
    def embeds(self) -> list[EmbedInfo]:
        return [e for b in self.blocks for e in b.embeds]

    @property
    def text(self) -> str:
        return " ".join(b.text_content for b in self.blocks if b.text_content)


@dataclass
class Issue:
    issue_type: str  # "image", "link", "heading", "flyer", "video", "emoji"
    category: str  # Short category label for summary
    description: str  # Plain English explanation
    suggestion: str  # Fix suggestion
    section_name: str
    element_selector: str  # CSS selector for screenshot
    screenshot_path: str = ""  # Filled in after screenshot capture
    current_alt: str = ""  # For displaying current alt text in styled box
    suggested_alt: str = ""  # For displaying suggested alt text in styled box
    missing_details: list[str] = field(default_factory=list)  # For flyer missing info
    extra_screenshots: list[str] = field(default_factory=list)  # Additional screenshot paths
    extra_suggested_alts: list[str] = field(default_factory=list)  # Per-image suggestions for duplicates


@dataclass
class PageData:
    url: str
    title: str
    sections: list[SmoreSection] = field(default_factory=list)
    all_headings: list[HeadingInfo] = field(default_factory=list)
