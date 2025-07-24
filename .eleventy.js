module.exports = function(eleventyConfig) {
  // Copy the TikTok verification file to the root of the output
  eleventyConfig.addPassthroughCopy("tiktokwOF0dOyzpe5EQNbz8DoTzdUq4GxgNYzB.txt");
  
  // Copy the CSS file
  eleventyConfig.addPassthroughCopy("src/css");

  // Configure markdown-it
  let markdownIt = require("markdown-it");
  let md = markdownIt({
    html: true,
    breaks: true,
    linkify: true
  });
  
  eleventyConfig.setLibrary("md", md);

  return {
    dir: {
      input: "src",
      output: "_site",
      includes: "_includes",
      layouts: "_layouts"
    },
    markdownTemplateEngine: "njk",
    htmlTemplateEngine: "njk"
  };
};
