# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2021 melon-ruet
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

import concurrent.futures
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup as BS
from operator import itemgetter
from typing import List, Optional

from cps import logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata

log = logger.create()


class Goodreads(Metadata):
    __name__ = "Goodreads"
    __id__ = "goodreads"
    BASE_URL = "https://www.goodreads.com"
    # headers = {"upgrade-insecure-requests": "1",
    #            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36",
    #            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    #            "sec-gpc": "1",
    #            "sec-fetch-site": "none",
    #            "sec-fetch-mode": "navigate",
    #            "sec-fetch-user": "?1",
    #            "sec-fetch-dest": "document",
    #            "accept-encoding": "gzip, deflate, br",
    #            "accept-language": "en-US,en;q=0.9"}
    session = requests.Session()

    # session.headers = headers

    def inner(self, link, index) -> [dict, int]:
        with self.session as session:
            try:
                r = session.get(f"https://www.amazon.com/{link}")
                r.raise_for_status()
            except Exception as ex:
                log.warning(ex)
                return
            long_soup = BS(r.text, "lxml")  # ~4sec :/
            soup2 = long_soup.find("div", attrs={
                "cel_widget_id": "dpx-books-ppd_csm_instrumentation_wrapper"})
            if soup2 is None:
                return
            try:
                match = MetaRecord(
                    title="",
                    authors="",
                    source=MetaSourceInfo(
                        id=self.__id__,
                        description="Amazon Books",
                        link="https://amazon.com/"
                    ),
                    url=f"https://www.amazon.com{link}",
                    # the more searches the slower, these are too hard to find in reasonable time or might not even exist
                    publisher="",  # very unreliable
                    publishedDate="",  # very unreliable
                    id=None,  # ?
                    tags=[]  # dont exist on amazon
                )

                try:
                    match.description = "\n".join(
                        soup2.find("div", attrs={
                            "data-feature-name": "bookDescription"}).stripped_strings) \
                                            .replace("\xa0", " ")[:-9].strip().strip("\n")
                except (AttributeError, TypeError):
                    return None  # if there is no description it is not a book and therefore should be ignored
                try:
                    match.title = soup2.find("span", attrs={"id": "productTitle"}).text
                except (AttributeError, TypeError):
                    match.title = ""
                try:
                    match.authors = [next(
                        filter(lambda i: i != " " and i != "\n" and not i.startswith("{"),
                               x.findAll(text=True))).strip()
                                     for x in
                                     soup2.findAll("span", attrs={"class": "author"})]
                except (AttributeError, TypeError, StopIteration):
                    match.authors = ""
                try:
                    match.rating = int(
                        soup2.find("span", class_="a-icon-alt").text.split(" ")[0].split(
                            ".")[
                            0])  # first number in string
                except (AttributeError, ValueError):
                    match.rating = 0
                try:
                    match.cover = \
                        soup2.find("img", attrs={"class": "a-dynamic-image frontImage"})[
                            "src"]
                except (AttributeError, TypeError):
                    match.cover = ""
                return match, index
            except Exception as e:
                log.error_or_exception(e)
                return

    def get_goodreads_search_url(self, query):
        title_tokens = list(self.get_title_tokens(query, strip_joiners=False))
        if title_tokens:
            tokens = [quote(t.encode("utf-8")) for t in title_tokens]
            query = "+".join(tokens)

        query = "search_type=books&search[query]=" + query
        return self.BASE_URL + "/search?" + query

    def query_goodreads(self, url):
        try:
            # results = self.session.get(url, headers=self.headers)
            response = self.session.get(url)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            log.error_or_exception(e)
            return None
        except Exception as e:
            log.warning(e)
            return None

        # Now grab the first value from the search results, provided the
        # title and authors appear to be for the same book
        # self._parse_search_results(log, title, authors, root, matches, timeout)

    def parse_book_urls(self, html_text):
        soup = BS(html_text, "html.parser")

        urls = []
        book_html_tags = soup.findAll("a", attrs={"class": "bookTitle", "itemprop": "url"})
        if not book_html_tags:
            log.warning("No book found or books parsing error")
            return urls

        for book_tag in book_html_tags:
            try:
                urls.append(self.BASE_URL + book_tag.attrs["href"])
            except KeyError as error:
                log.error("Book url can not parsed", exc_info=error)

        return urls

    def get_book_metarecord(self, url):
        response = self.query_goodreads(url)
        soup = BS(response.text, "html.parser")
        title = soup.find("h1", attrs={"id": "bookTitle"}).text.strip("\n").strip()
        authors = [
            author_tag.find("span").text.strip("\n").strip()
            for author_tag in
            soup.find("div", attrs={"id": "bookAuthors"}).findAll("div", attrs={"class": "authorName__container"})
        ]
        cover = soup.find("img", attrs={"id": "coverImage"})["src"]
        description = soup.find("div", attrs={"id": "description"})
        description =    description.find("span", attrs={"style": "display: none"})
        return MetaRecord(
            id="",
            title=title,
            authors=authors,
            url=url,
            source=MetaSourceInfo(
                id=self.__id__,
                description="Goodreads",
                link=self.BASE_URL
            ),
            cover=cover,
            description=description,
            series="",
            series_index=None,
            identifiers={},
            publisher="",
            publishedDate="",
            rating=0,
            languages=[],
            tags=[]
        )

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        val = []
        if self.active:
            query_url = self.get_goodreads_search_url(query)
            response = self.query_goodreads(query_url)
            if response:
                urls = self.parse_book_urls(response.text)
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    fut = {executor.submit(self.get_book_metarecord, url) for url in urls}
                    val = list(map(lambda x: x.result(), concurrent.futures.as_completed(fut)))
            else:
                log.error("Goodreads get books failed")

        return val


if __name__ == "__main__":
    goodreads = Goodreads()
    res = goodreads.search(
        "A Mind for Numbers: How to Excel at Math and Science (Even If You Flunked Algebra)")
