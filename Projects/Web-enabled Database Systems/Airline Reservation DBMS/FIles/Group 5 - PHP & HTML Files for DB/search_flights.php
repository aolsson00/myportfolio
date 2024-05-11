<?php

include 'db.php';


if (isset($_POST['depart_city']) && $_POST['depart_city'] !== 'default' || isset($_POST['arrival_city']) && $_POST['arrival_city'] !== 'default') {
    $depart_city = isset($_POST['depart_city']) && $_POST['depart_city'] !== 'default' ? $_POST['depart_city'] : null;
    $arrival_city = isset($_POST['arrival_city']) && $_POST['arrival_city'] !== 'default' ? $_POST['arrival_city'] : null;

    try {
      
        $conn->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

        
        $query = "SELECT * FROM flight WHERE depart_city = :depart_city AND arrival_city = :arrival_city";

        
        $stmt = $conn->prepare($query);

       
        $stmt->bindParam(':depart_city', $depart_city);
        $stmt->bindParam(':arrival_city', $arrival_city);

        // Execute the query
        $stmt->execute();

       
        if ($stmt->rowCount() > 0) {
            // Display the table header
            echo "<h2>Flights from $depart_city to $arrival_city</h2>";
            echo "<table border='1'>";
            echo "<tr><th>Flight Number</th><th>Departure City</th><th>Arrival City</th><th>Departure Date</th><th>Arrival Date</th><th>Departure Time</th><th>Arrival Time</th><th>Price</th></tr>";

           
            while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
                echo "<tr>";
                echo "<td>" . $row['flight_num'] . "</td>";
                echo "<td>" . $row['depart_city'] . "</td>";
                echo "<td>" . $row['arrival_city'] . "</td>";
                echo "<td>" . $row['dep_date'] . "</td>";
                echo "<td>" . $row['arr_date'] . "</td>";
                echo "<td>" . $row['dep_time'] . "</td>";
                echo "<td>" . $row['arr_time'] . "</td>";
                echo "<td>" . $row['price'] . "</td>";
                echo "</tr>";
            }

            echo "</table>";
            echo "<p><a href='input_ticket_form.html'>Proceed to Ticket Payment</a></p>";
        } else {
            echo "No flights found from $depart_city to $arrival_city";
        }
    } catch (PDOException $e) {
        echo "Error: " . $e->getMessage();
    }
} else {
    echo "Please select a departing city and an arriving city.";
}


$conn = null;
?>
