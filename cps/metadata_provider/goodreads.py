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
import datetime
import re
from typing import List, Optional
from urllib.parse import quote

import pytz
import requests
from bs4 import BeautifulSoup as BS

from cps import logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata

log = logger.create()


class Goodreads(Metadata):
    __name__ = "Goodreads"
    __id__ = "goodreads"
    BASE_URL = "https://www.goodreads.com"
    session = requests.Session()

    def get_goodreads_search_url(self, query):
        title_tokens = list(self.get_title_tokens(query, strip_joiners=False))
        if title_tokens:
            tokens = [quote(t.encode("utf-8")) for t in title_tokens]
            query = "+".join(tokens)

        query = "search_type=books&search[query]=" + query
        return self.BASE_URL + "/search?" + query

    def query_goodreads(self, url):
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            log.error_or_exception(e)
            return None
        except Exception as e:
            log.warning(e)
            return None

    def parse_book_urls(self, html_text):
        soup = BS(html_text, "html.parser")

        urls = []
        book_html_tags = soup.findAll(
            "a", attrs={"class": "bookTitle", "itemprop": "url"})
        if not book_html_tags:
            log.warning("No book found or books parsing error")
            return urls

        for book_tag in book_html_tags:
            try:
                urls.append(self.BASE_URL + book_tag.attrs["href"])
            except KeyError as error:
                log.error("Book url can not parsed", exc_info=error)

        return urls

    def _convert_date_text(self, date_text):
        # Note that the date text could be "2003", "December 2003" or "December 10th 2003"
        year = int(date_text[-4:])
        month = 1
        day = 1
        if len(date_text) > 4:
            text_parts = date_text[:len(date_text) - 5].partition(' ')
            month_name = text_parts[0]
            # Need to convert the month name into a numeric value
            # For now I am "assuming" the Goodreads website only displays in English
            # If it doesn't will just fallback to assuming January
            month_dict = {
                "January": 1, "February": 2, "March": 3, "April": 4, "May": 5,
                "June": 6,
                "July": 7, "August": 8, "September": 9, "October": 10,
                "November": 11, "December": 12
            }
            month = month_dict.get(month_name, 1)
            if len(text_parts[2]) > 0:
                day = int(re.match('(\d+)', text_parts[2]).groups(0)[0])
        return datetime.datetime(year, month, day, tzinfo=pytz.UTC)

    def parse_publisher_and_date(self, soup):
        publisher = None
        pub_date = None
        publisher_node = soup.find("div", attrs={"id": "metacol"}).find(
            "div", attrs={"id": "details"})
        if publisher_node:
            # Publisher is specified within the div above with variations of:
            #  Published December 2003 by Books On Tape <nobr class="greyText">(first published 1982)</nobr>
            #  Published June 30th 2010
            # Note that the date could be "2003", "December 2003" or "December 10th 2003"
            publisher_node_text = publisher_node.findAll("div")[1].text
            # See if we can find the publisher name
            pub_text_parts = publisher_node_text.partition(' by ')
            if pub_text_parts[2]:
                publisher = pub_text_parts[2].strip()
                if '(first' in publisher:
                    # The publisher name is followed by (first published xxx) so strip that off
                    publisher = publisher.rpartition('(first')[0].strip()

            # Now look for the pubdate. There should always be one at start of the string
            pubdate_text_match = re.search('Published[\n\s]*([\w\s]+)',
                                           pub_text_parts[0].strip())
            pubdate_text = None
            if pubdate_text_match is not None:
                pubdate_text = pubdate_text_match.groups(0)[0]
            # If we have a first published section of text use that for the date.
            if '(first' in publisher_node_text:
                # For the publication date we will use first published date
                # Note this date could be just a year, or it could be monthname year
                pubdate_text_match = re.search('.*\(first published ([\w\s]+)',
                                               publisher_node_text)
                if pubdate_text_match is not None:
                    first_pubdate_text = pubdate_text_match.groups(0)[0]
                    if pubdate_text and first_pubdate_text[-4:] == pubdate_text[-4:]:
                        # We have same years, use the first date as it could be more accurate
                        pass
                    else:
                        pubdate_text = first_pubdate_text
            if pubdate_text:
                pub_date = self._convert_date_text(pubdate_text)
        return publisher, pub_date

    def parse_rating(self, soup):
        rating_node = soup.find("span", attrs={"itemprop": "ratingValue"})
        if rating_node and len(rating_node) > 0:
            try:
                rating_text = rating_node.text.strip("\n").strip()
                return float(rating_text)
            except (AttributeError, ValueError):
                log.error("parse_rating: Exception getting rating")
        return None

    def get_book_metarecord(self, url):
        response = self.query_goodreads(url)
        soup = BS(response.text, "html.parser")
        title = soup.find("h1", attrs={"id": "bookTitle"}).text.strip("\n").strip()
        authors = [
            author_node.find("span").text.strip("\n").strip()
            for author_node in
            soup.find("div", attrs={"id": "bookAuthors"}).findAll("div", attrs={
                "class": "authorName__container"})
        ]
        description = soup.find("div", attrs={"id": "description"})
        description = description.find("span", attrs={"style": "display: none"})
        series = soup.find("h2", attrs={"id": "bookSeries"}).find(
            "a", attrs={"class": "greyText"})
        series_index = None
        if series:
            series_splitted = series.text.split("#")
            series = series_splitted[0].strip("\n").strip()
            try:
                series_index = int(series_splitted[1].strip("\n").strip())
            except (IndexError, ValueError):
                log.error("Can not retrieve series index")
        publisher, publish_date = self.parse_publisher_and_date(soup)
        rating = self.parse_rating(soup)
        meta_record = MetaRecord(
            id="",
            title=title,
            authors=authors,
            url=url,
            source=MetaSourceInfo(
                id=self.__id__,
                description="Goodreads",
                link=self.BASE_URL
            ),
            description=description,
            series=series,
            series_index=series_index,
            identifiers={},
            publisher=publisher,
            publishedDate=publish_date,
            rating=rating,
            languages=[],
            tags=[]
        )

        cover = soup.find("img", attrs={"id": "coverImage"})
        if cover:
            meta_record.cover = cover["src"]

        return meta_record

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
                    val = list(
                        map(lambda x: x.result(), concurrent.futures.as_completed(fut)))
            else:
                log.error("Goodreads get books failed")

        return val

#
# if __name__ == "__main__":
#     goodreads = Goodreads()
#     res = goodreads.search(
#         "A Mind for Numbers: How to Excel at Math and Science (Even If You Flunked Algebra)")
