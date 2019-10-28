package refdiff.examples;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.function.BiConsumer;
import org.eclipse.jgit.lib.Repository;
import org.eclipse.jgit.revwalk.RevCommit;
import refdiff.core.RefDiff;
import refdiff.core.diff.CstDiff;
import refdiff.core.diff.Relationship;
import refdiff.core.diff.RelationshipType;
import refdiff.core.io.GitHelper;
import refdiff.parsers.java.JavaParser;
import refdiff.core.cst.Location;
import java.util.List;
import java.util.stream.Collectors;

public class RefDiffExample {
	
	public static void main(String[] args) throws Exception {
		runExamples();
	}
	
	private static void runExamples() throws Exception {
		// This is a temp folder to clone or checkout git repositories.
		File tempFolder = new File("temp");
		
		// Now, we use the parser for Java.
		JavaParser javaParser = new JavaParser(tempFolder);
		RefDiff refDiffJava = new RefDiff(javaParser);
		
		File hiveRepoFile = refDiffJava.cloneGitRepository(
			new File(tempFolder, "hive"),
			"https://github.com/apache/hive.git");


		BiConsumer<RevCommit, CstDiff> refactorings = (commitRev, diffForCommit) ->
//			printRefactorings(
//			String.format("Refactorings found in hive commit %s", commitRev.abbreviate(40).name()), diffForCommit);
			saveRefactoringInfo(commitRev, diffForCommit);


		GitHelper gitHelper = new GitHelper();
		Repository repo = GitHelper.openRepository(hiveRepoFile);
		// HEAD -> refs/heads/master
		int numCommits = gitHelper.countCommits(repo,"HEAD");
//		System.out.println(numCommits);

		refDiffJava.computeDiffForCommitHistory(hiveRepoFile, numCommits, refactorings);
	}

	private static void saveRefactoringInfo(RevCommit commit, CstDiff diff){
		final List<RevisionRefactor> revisionRefactors = new ArrayList<>();

		String revision = commit.abbreviate(40).name();

		for (Relationship rel : diff.getRefactoringRelationships()) {
			RelationshipType type = rel.getType();
			Location loc = rel.getNodeAfter().getLocation();

			String file = loc.getFile();
			int beginLine = loc.getBeginLine();
			int endLine = loc.getEndLine();

			// save
			revisionRefactors.add(new RevisionRefactor(revision, file, type.name(), beginLine, endLine));
		}


		String csvContent = revisionRefactors.stream()
				.map(RevisionRefactor::toString)
				.collect(Collectors.joining("\n"));
		if (csvContent.isEmpty()){
			csvContent = "";
		} else {
			csvContent += "\n";
		}
		try (BufferedWriter writer = new BufferedWriter(new FileWriter("refactorings.csv", true))){
			writer.write(csvContent);
		} catch (IOException e) {
			e.printStackTrace();
		}
	}
	
	private static void printRefactorings(String headLine, CstDiff diff) {
		System.out.println(headLine);
		for (Relationship rel : diff.getRefactoringRelationships()) {
//			System.out.println(rel.getStandardDescription());

			RelationshipType type = rel.getType();
			Location loc = rel.getNodeAfter().getLocation();
			String file = loc.getFile();
			int beginLine = loc.getBeginLine();
			int endLine = loc.getEndLine();

			String output = String.format("%s %s: %d - %d", type, file, beginLine, endLine);
			System.out.println(output);

		}
	}


}
class RevisionRefactor {
	private final String revision;
	private final String fileName;
	private final String refType;
	private final int startLine;
	private final int endLine;

	public RevisionRefactor(String revision, String fileName, String refType, int startLine, int endLine) {
		this.revision = revision;
		this.fileName = fileName;
		this.refType = refType;
		this.startLine = startLine;
		this.endLine = endLine;
	}

	public String getRevision() {
		return revision;
	}

	public String getFileName() {
		return fileName;
	}

	public String getRefType() {
		return refType;
	}

	public int getStartLine() {
		return startLine;
	}

	public int getEndLine() {
		return endLine;
	}

	@Override
	public String toString() {
		return revision + "," + fileName + "," + refType + "," + startLine + "," + endLine;
	}
}
