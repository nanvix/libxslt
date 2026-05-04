/* Minimal libxslt integration test for Nanvix. */
#include <libxml/parser.h>
#include <libxslt/xslt.h>
#include <libxslt/xsltutils.h>
#include <libxslt/transform.h>

static const char *TEST_XML = "<root><item>hello</item></root>";
static const char *TEST_XSL =
    "<?xml version=\"1.0\"?>"
    "<xsl:stylesheet version=\"1.0\" xmlns:xsl=\"http://www.w3.org/1999/XSL/Transform\">"
    "<xsl:template match=\"/\">"
    "<output><xsl:value-of select=\"/root/item\"/></output>"
    "</xsl:template>"
    "</xsl:stylesheet>";

int main(void) {
    xmlDocPtr xml_doc, xsl_doc, result;
    xsltStylesheetPtr stylesheet;

    xmlInitParser();
    xsltInit();

    xml_doc = xmlParseMemory(TEST_XML, (int)__builtin_strlen(TEST_XML));
    if (!xml_doc) return 1;

    xsl_doc = xmlParseMemory(TEST_XSL, (int)__builtin_strlen(TEST_XSL));
    if (!xsl_doc) { xmlFreeDoc(xml_doc); return 1; }

    stylesheet = xsltParseStylesheetDoc(xsl_doc);
    if (!stylesheet) { xmlFreeDoc(xml_doc); return 1; }

    result = xsltApplyStylesheet(stylesheet, xml_doc, NULL);
    if (!result) { xsltFreeStylesheet(stylesheet); xmlFreeDoc(xml_doc); return 1; }

    xmlFreeDoc(result);
    xsltFreeStylesheet(stylesheet);
    xmlFreeDoc(xml_doc);
    xsltCleanupGlobals();
    xmlCleanupParser();
    return 0;
}
