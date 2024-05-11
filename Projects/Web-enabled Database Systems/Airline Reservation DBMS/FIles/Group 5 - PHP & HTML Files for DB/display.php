<?php

function display($query_string){ 
global $conn; 
$stmt = $conn->prepare($query_string); 
$stmt->execute(); 
$stmt->setFetchMode(PDO::FETCH_NUM); 


$num_c = $stmt->columnCount();
 

echo "<table border=1>\n"; 


echo "<tr>"; 
for ($i = 0; $i < $num_c; $i++) { 
	$col = $stmt->getColumnMeta($i); 
	echo "<th>" . $col['name'] . "</th>"; 
}
echo "</tr>"; 

# display table content 
while($row = $stmt->fetch()){ 
	echo "<tr>"; 
	$i = 0; 
	while($i < $num_c) 
	{ 
		echo "<td>". $row[$i]. "</td>"; 
		$i++; 
	}
	echo "</tr>"; 
}

echo "</table>"; 
}

?>
